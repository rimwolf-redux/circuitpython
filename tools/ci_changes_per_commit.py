#! /usr/bin/env python3

# SPDX-FileCopyrightText: 2021 microDev
#
# SPDX-License-Identifier: MIT

# GraphQL Query

QUERY_COMMITS = """
query ($owner: String!, $name: String!, $pullNumber: Int!, $commitsPerPage: Int!, $beforeCommit: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $pullNumber) {
      commits(last: $commitsPerPage, before: $beforeCommit) {
        totalCount
        pageInfo {
          startCursor
          hasPreviousPage
        }
        nodes {
          commit {
            checkSuites(first: 100) {
              nodes {
                conclusion
                workflowRun {
                  workflow {
                    name
                  }
                }
                id
              }
              totalCount
            }
            oid
          }
        }
      }
    }
  }
}
"""

QUERY_CHECK_RUNS = """
query ($checkSuiteID: ID!,
       $afterFailedRun: String, $afterIncompleteRun: String,
       $includeFailedRuns: Boolean!, $includeIncompleteRuns: Boolean!) {
  node(id: $checkSuiteID) {
    ... on CheckSuite {
      failedRuns: checkRuns(
        first: 100
        after: $afterFailedRun
        filterBy: {checkType: LATEST, conclusions: [ACTION_REQUIRED, TIMED_OUT, CANCELLED, FAILURE, NEUTRAL, STARTUP_FAILURE]}
      ) @include(if: $includeFailedRuns) {
        nodes {
          name
        }
        pageInfo {
          endCursor
          hasNextPage
        }
      }
      incompleteRuns: checkRuns(
        first: 100
        after: $afterIncompleteRun
        filterBy: {checkType: LATEST, statuses: [QUEUED, IN_PROGRESS, WAITING, PENDING, REQUESTED]}
      ) @include(if: $includeIncompleteRuns) {
        nodes {
          name
        }
        pageInfo {
          endCursor
          hasNextPage
        }
      }
    }
  }
}
"""


import os
import re
import json
import requests


query_variables_commits = {
    "owner": "",
    "name": "",
    "pullNumber": int(os.environ["PULL"]),
    "commitsPerPage": 20,
    "beforeCommit": None,
}


query_variables_check_runs = {
    "checkSuiteID": "",
    "afterFailedRun": None,
    "afterIncompleteRun": None,
    "includeFailedRuns": True,
    "includeIncompleteRuns": True,
}


headers = {"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}"}


class Query:
    def __init__(self, query, variables={}, headers={}):
        self.query = query
        self.variables = variables
        self.headers = headers

    def paginate(self, page_info, name):
        has_page = (
            page_info["hasNextPage"] if name.startswith("after") else page_info["hasPreviousPage"]
        )
        if has_page:
            self.variables[name] = (
                page_info["endCursor"] if name.startswith("after") else page_info["startCursor"]
            )
        return has_page

    def fetch(self):
        request = requests.post(
            "https://api.github.com/graphql",
            json={"query": self.query, "variables": self.variables},
            headers=self.headers,
        )
        if request.status_code == 200:
            return request.json()
        else:
            print(request.json())
            raise Exception("Query Failed: {}".format(request.status_code))


def set_output(name, value):
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "at") as f:
            print(f"{name}={value}", file=f)
    else:
        print(f"Would set GitHub actions output {name} to '{value}'")


def get_commit_depth_and_check_suite(query_commits):
    while True:
        commits = query_commits.fetch()["data"]["repository"]["pullRequest"]["commits"]

        if commits["totalCount"] > 0:
            nodes = commits["nodes"]
            nodes.reverse()
            if nodes[0]["commit"]["oid"] == os.environ["EXCLUDE_COMMIT"]:
                nodes.pop(0)
            for index, commit in enumerate(nodes):
                commit = commit["commit"]
                commit_sha = commit["oid"]
                check_suites = commit["checkSuites"]
                if check_suites["totalCount"] > 0:
                    for check_suite in check_suites["nodes"]:
                        if check_suite["workflowRun"]["workflow"]["name"] == "Build CI":
                            return [
                                {"sha": commit_sha, "depth": index + 1},
                                check_suite["id"]
                                if check_suite["conclusion"] != "SUCCESS"
                                else None,
                            ]
            else:
                if not query_commits.paginate(commits["pageInfo"], "beforeCommit"):
                    break

    return [None, None]


def append_runs_to_list(runs, bad_runs_by_matrix):
    regex_matrix = re.compile("^build-[^ ]+")
    regex_board = re.compile("\([^ ]+\)$")
    for run in runs["nodes"]:
        name = run["name"]
        res_matrix = regex_matrix.search(name)
        if res_matrix:
            matrix = res_matrix.group()
            if matrix not in bad_runs_by_matrix:
                bad_runs_by_matrix[matrix] = []
            res_board = regex_board.search(name)
            if res_board:
                bad_runs_by_matrix[matrix].append(res_board.group()[1:-1])


def get_bad_check_runs(query_check_runs):
    more_pages = True
    bad_runs_by_matrix = {}
    while more_pages:
        check_runs = query_check_runs.fetch()["data"]["node"]
        run_types = ["failed", "incomplete"]
        more_pages = False

        for run_type in run_types:
            run_type_camel = run_type.capitalize() + "Run"
            run_type = run_type + "Runs"

            append_runs_to_list(check_runs[run_type], bad_runs_by_matrix)

            if query_check_runs.paginate(
                check_runs[run_type]["pageInfo"], "after" + run_type_camel
            ):
                query_check_runs.variables["include" + run_type_camel] = True
                more_pages = True

    return bad_runs_by_matrix


def set_commit(commit):
    set_output("commit_sha", commit["sha"])
    set_output("commit_depth", commit["depth"])


def main():
    query_commits = Query(QUERY_COMMITS, query_variables_commits, headers)
    query_commits.variables["owner"], query_commits.variables["name"] = os.environ["REPO"].split(
        "/"
    )

    commit, check_suite = get_commit_depth_and_check_suite(query_commits)

    if check_suite is None:
        if commit is None:
            print("Abort: No check suite found")
        else:
            set_commit(commit)
        quit()

    query_check_runs = Query(QUERY_CHECK_RUNS, query_variables_check_runs, headers)
    query_check_runs.variables["checkSuiteID"] = check_suite

    check_runs = get_bad_check_runs(query_check_runs)

    if len(check_runs) == 0:
        print("Abort: No check runs found")
        quit()

    set_commit(commit)
    set_output("check_runs", json.dumps(check_runs))


if __name__ == "__main__":
    main()
