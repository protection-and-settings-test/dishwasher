#!/usr/bin/env python3

import sys
import os
import requests
import json
import yaml
from pprint import pprint

gh_pat = os.environ['PAT']

# Load the config files
with open("sync_forks_and_set_policies_config.yml", "r") as config_file:
  config = yaml.safe_load(config_file)

gh_username = config["run_as"]
gh_organization = config["organization"]
rate_limit_remaining = 0
more_pages = True
page = 1
synced_repos = []
unsynced_repos = []
unprotected_repos = []
actions_enabled_repos = []

# Set the authorization header
header = {
    "Accept": "application/vnd.github+json",
    "Authorization": "token %s" % gh_pat
}

# Get all repos in the org
while more_pages:
  repos_response = requests.get(
      "https://api.github.com/orgs/%s/repos?per_page=100&page=%s&type=forks&sort=full_name" % (
          gh_organization,
          page),
      headers = header
  )

  rate_limit_remaining = repos_response.headers["X-RateLimit-Remaining"]

  if repos_response.status_code != 200:
    print("ERROR: Could not retrieve repos for organization: %s\n%s" % (
        gh_organization,
        repos_response.json()["message"]
        )
    )
    sys.exit(1)

  # Walk through the repos
  for repo in repos_response.json():

    # Sync against upstream unless excluded
    if not repo["name"] in config["overrides"]["do_not_sync"]:
      upstream_response = requests.post(
          "https://api.github.com/repos/%s/%s/merge-upstream" % (
              gh_organization,
              repo["name"]),
          headers = header,
          data = json.dumps({
              "branch": repo["default_branch"]
              }
          )
      )

      rate_limit_remaining = upstream_response.headers["X-RateLimit-Remaining"]

      # Check whether the repo sync was successful
      if upstream_response.status_code != 200:
        unsynced_repos.append({
            "repo": repo["name"],
            "message": upstream_response.json()["message"]
            }
        )
      else:
        synced_repos.append({
            "repo": repo["name"],
            "message": upstream_response.json()["message"]
            }
        )

    else:
      print("     - Excluded from sync by config.yml")
      unsynced_repos.append({
          "repo": repo["name"],
          "message": "Excluded from sync by policy."
          }
      )

    # Branch protection rules are set at the same time; track whether we need to make the API call
    update_branch_protection = False

    # Get details of branch protection
    protection_details_response = requests.get(
        "https://api.github.com/repos/%s/%s/branches/%s/protection" % (
            gh_organization,
            repo["name"],
            repo["default_branch"]
        ),
        headers = header)

    rate_limit_remaining = protection_details_response.headers["X-RateLimit-Remaining"]

    # The branch protection API returns a bunch of extraneous information, and requires parameters
    # that are not relevant to this task. Extract the information we need if it exists, and if it
    # doesn't, set a default value that will cause a correct value to be set.

    protection_details = protection_details_response.json()

    # Not controlled by policy

    if "required_status_checks" in protection_details:
      required_status_checks = protection_details["required_status_checks"]
    else:
      required_status_checks = None

    if "enforce_admins" in protection_details:
      enforce_admins = protection_details["enforce_admins"]["enabled"]
    else:
      enforce_admins = None

    if "restrictions" in protection_details:
      restrictions = protection_details["restrictions"]
    else:
      restrictions = None

    # Controlled by policy

    # Set a value that would be corrected if it doesn't already exist
    required_approving_review_count = 1

    if "required_pull_request_reviews" in protection_details and \
        "required_approving_review_count" in protection_details["required_pull_request_reviews"]:

      required_approving_review_count = protection_details["required_pull_request_reviews"]["required_approving_review_count"]

    # Set a value that would be corrected if it doesn't already exist
    allow_force_pushes = True

    if "allow_force_pushes" in protection_details:
      allow_force_pushes = protection_details["allow_force_pushes"]["enabled"]

    # Set a value that would be corrected if it doesn't already exist
    allow_deletions = True

    if "allow_deletions" in protection_details:
      allow_deletions = protection_details["allow_deletions"]["enabled"]

    # Check the branch protection policies

    # Force pushes to primary branch should be disabled
    if allow_force_pushes:

      if repo["name"] not in config["overrides"]["do_not_disable_force_pushes"]:
        # If not excluded, set the proper value.
        update_branch_protection = True
        allow_force_pushes = False

        unprotected_repos.append({
            "repo": repo["name"],
            "message": "Force pushes were disabled."
            }
        )

      else:
        # If excluded, keep the existing value but notify
        unprotected_repos.append({
            "repo": repo["name"],
            "message": "Force pushes left enabled (excluded from policy)."
            }
        )

    # Branch deletion should be disabled
    if allow_deletions:

      if repo["name"] not in config["overrides"]["do_not_disable_deletion"]:
        # If not excluded, set the proper value.
        update_branch_protection = True
        allow_deletions = False

        unprotected_repos.append({
            "repo": repo["name"],
            "message": "Ability to delete branches was disabled."
            }
        )

      else:
        # If excluded, keep the existing value but notify
        unprotected_repos.append({
            "repo": repo["name"],
            "message": "Ability to delete branches left enabled (excluded from policy)."
            }
        )

    # Pull request reviews should be at max to discourage merging into the fork
    if required_approving_review_count != 6:

      if repo["name"] not in config["overrides"]["do_not_enforce_merge_reviews"]:
        # If not excluded, set the proper value. If excluded, leave it as is
        update_branch_protection = True
        required_approving_review_count = 6

        unprotected_repos.append({
            "repo": repo["name"],
            "message": "Required approving review count set to 6."
            }
        )

      else:
        # If excluded, keep the existing value but notify
        unprotected_repos.append({
            "repo": repo["name"],
            "message": "Required approving review count unchanged (excluded from policy)"
            }
        )

    # Process the accumulated changes to branch protection
    if update_branch_protection:
      update_branch_protection_response = requests.put(
          "https://api.github.com/repos/%s/%s/branches/%s/protection" % (
              gh_organization,
              repo["name"],
              repo["default_branch"]),
          headers = header,
          data = json.dumps({
              "required_status_checks": required_status_checks,
              "enforce_admins": enforce_admins,
              "required_pull_request_reviews": {
                  "required_approving_review_count": required_approving_review_count
                  },
              "restrictions": restrictions,
              "allow_force_pushes": allow_force_pushes,
              "allow_deletions": allow_deletions
              }
          )
      )

    # Verify actions are disabled on forks unless excluded
    actions_response = requests.get(
        "https://api.github.com/repos/%s/%s/actions/permissions" % (
            gh_organization,
            repo["name"]),
        headers = header)

    rate_limit_remaining = actions_response.headers["X-RateLimit-Remaining"]

    if actions_response.status_code != 200:
      print("Could not fetch actions status for %s" % repo["name"])
      sys.exit(1)

    if actions_response.json()["enabled"]:

      if not repo["name"] in config["overrides"]["do_not_disable_actions"]:
        # If not excluded, set the proper value. If excluded, leave it as is
        disable_actions_response = requests.put(
            "https://api.github.com/repos/%s/%s/actions/permissions" % (
                gh_organization,
                repo["name"]),
            headers = header,
            data = json.dumps({
                "enabled": False
                }
            )
        )

        actions_enabled_repos.append({
            "repo": repo["name"],
            "message": "Actions were disabled."
            }
        )

      else:
        # If excluded, keep the existing value
        actions_enabled_repos.append({
            "repo": repo["name"],
            "message": "Actions were left enabled (excluded from policy)"
            }
        )

  if "next" in repos_response.links:
    page += 1
  else:
    more_pages = False

if synced_repos:
  print("\nUpstream sync succeeded:")
  for synced_repo in synced_repos:
    print(" * %s" % synced_repo["repo"])

if unsynced_repos:
  print("\nRepos failed to sync:")
  for unsynced_repo in unsynced_repos:
    print(" * %s: %s" % (unsynced_repo["repo"],unsynced_repo["message"]))

if unprotected_repos:
  print("\nRepos with branch protection issues:")
  for unprotected_repo in unprotected_repos:
    print(" * %s: %s" % (unprotected_repo["repo"],unprotected_repo["message"]))

if actions_enabled_repos:
  print("\nRepos with Actions enabled:")
  for actions_enabled_repo in actions_enabled_repos:
    print(" * %s %s" % (actions_enabled_repo["repo"],actions_enabled_repo["message"]))

#print("Rate limit remaining: %s" % rate_limit_remaining)
