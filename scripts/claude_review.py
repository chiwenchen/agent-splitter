#!/usr/bin/env python3
"""Claude PR code reviewer — submits APPROVE or REQUEST_CHANGES via GitHub API."""

import json
import os
import sys
import urllib.request

import anthropic

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
REPO = os.environ["GITHUB_REPOSITORY"]
PR_NUMBER = os.environ["PR_NUMBER"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]


def github_request(method, path, body=None):
    url = f"https://api.github.com{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def get_pr_diff():
    url = f"https://api.github.com/repos/{REPO}/pulls/{PR_NUMBER}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.diff",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req) as resp:
        return resp.read().decode()


def review_with_claude(diff):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": f"""You are reviewing a pull request for a Python expense-splitting service (Lambda + API Gateway).

Review this diff for:
- Correctness: logic bugs, edge cases, off-by-one errors
- Security: injection, data validation, auth issues
- Code quality: clarity, error handling

Respond with EXACTLY this JSON format (no markdown, no extra text):
{{
  "verdict": "APPROVE" or "REQUEST_CHANGES",
  "summary": "one or two sentence summary"
}}

Use APPROVE if the code looks correct and safe.
Use REQUEST_CHANGES only for real bugs or security issues — not style preferences.

Diff:
{diff[:8000]}""",
            }
        ],
    )

    text = response.content[0].text.strip()
    return json.loads(text)


def submit_review(verdict, summary):
    github_request(
        "POST",
        f"/repos/{REPO}/pulls/{PR_NUMBER}/reviews",
        {"event": verdict, "body": f"**Claude Code Review**\n\n{summary}"},
    )


def main():
    print(f"Reviewing PR #{PR_NUMBER} in {REPO}")
    diff = get_pr_diff()
    if not diff.strip():
        print("Empty diff, skipping review")
        sys.exit(0)

    result = review_with_claude(diff)
    verdict = result["verdict"]
    summary = result["summary"]

    print(f"Verdict: {verdict}")
    print(f"Summary: {summary}")

    submit_review(verdict, summary)
    print("Review submitted.")

    if verdict == "REQUEST_CHANGES":
        sys.exit(1)


if __name__ == "__main__":
    main()
