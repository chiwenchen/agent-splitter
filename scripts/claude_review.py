"""Claude PR code reviewer — posts a review comment on GitHub PRs."""

import json
import os
import subprocess
import urllib.request

import anthropic


def get_diff() -> str:
    base_sha = os.environ["BASE_SHA"]
    result = subprocess.run(
        ["git", "diff", f"{base_sha}...HEAD"],
        capture_output=True,
        text=True,
    )
    return result.stdout


def post_comment(body: str) -> None:
    token = os.environ["GITHUB_TOKEN"]
    repo = os.environ["REPO"]
    pr_number = os.environ["PR_NUMBER"]

    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    data = json.dumps({"body": body}).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/vnd.github+json")

    with urllib.request.urlopen(req) as resp:
        print(f"Comment posted (HTTP {resp.status})")


def main() -> None:
    diff = get_diff()

    if not diff.strip():
        print("No diff found, skipping review")
        return

    if len(diff) > 100_000:
        diff = diff[:100_000] + "\n... (diff truncated due to size)"

    client = anthropic.Anthropic()

    review_parts = []
    with client.messages.stream(
        model="claude-opus-4-6",
        max_tokens=2048,
        thinking={"type": "adaptive"},
        system="""You are an expert code reviewer. Review the given PR diff and provide concise, actionable feedback.

Focus on:
- Bugs or logical errors
- Security vulnerabilities
- Python best practices
- Missing edge cases or validation

If the code looks good, say so briefly. Be direct and specific.
Format your response in markdown. Start with a one-line summary, then list any issues with file references.""",
        messages=[
            {
                "role": "user",
                "content": f"Please review this PR diff:\n\n```diff\n{diff}\n```",
            }
        ],
    ) as stream:
        for text in stream.text_stream:
            review_parts.append(text)
            print(text, end="", flush=True)

    print()

    review = "".join(review_parts)
    body = f"## Claude Code Review 🤖\n\n{review}\n\n---\n*Reviewed by claude-opus-4-6*"
    post_comment(body)


if __name__ == "__main__":
    main()
