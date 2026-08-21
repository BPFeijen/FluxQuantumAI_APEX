# AWS CLI setup runbook (Windows)

**Purpose**: install, authenticate, and use AWS CLI v2 on Windows. **Zero secrets in this document**. Replace placeholders (`<ANGLE_BRACKETS>`) with environment-specific values when you run the commands.

---

## 1. Install / verify AWS CLI v2

```powershell
# Install (one-time)
winget install -e --id Amazon.AWSCLI

# Verify
aws --version
# Expected: aws-cli/2.x.x Python/3.x.x Windows/10
```

If `aws` is not on PATH, restart the shell (or log out / log in).

---

## 2. Pick an authentication method

| Method | When to use | Credential lifetime |
|---|---|---|
| **AWS SSO / IAM Identity Center** | Humans logging in interactively | Hours (auto-rotated) |
| **IAM Roles for EC2/ECS/Lambda** | The CLI runs on AWS infra | Auto-rotated by AWS |
| **`aws configure` (long-lived keys)** | Legacy, scripts that can't use SSO | Permanent until rotated |
| **Environment variables** | CI/CD or short-lived sessions | Process lifetime |

**Default recommendation**: SSO for humans, IAM roles for services. Long-lived keys only when neither is available.

---

## 3. Method A — AWS SSO (recommended for human use)

### One-time setup

```powershell
aws configure sso
```

Prompts:
- **SSO session name**: `<friendly-session-name>`
- **SSO start URL**: `https://<your-org>.awsapps.com/start`
- **SSO region**: `<your-sso-region>` (e.g. `us-east-1`)
- **SSO registration scopes**: `sso:account:access`
- Browser opens → approve.
- Choose account + role from the list.
- **CLI default region**: `<your-aws-region>` (e.g. `us-east-1`)
- **CLI default output format**: `json`
- **CLI profile name**: `<profile-name>` (e.g. `fluxquantum-dev`)

### Daily use

```powershell
# Refresh credentials when expired
aws sso login --profile <profile-name>

# Use the profile
aws sts get-caller-identity --profile <profile-name>

# Or set as default for the session
$env:AWS_PROFILE = "<profile-name>"
aws s3 ls
```

Credentials live in `%USERPROFILE%\.aws\sso\cache\` (auto-managed). Profile config in `%USERPROFILE%\.aws\config`.

---

## 4. Method B — IAM role on EC2/ECS/Lambda

If the CLI runs on AWS infrastructure with an attached role, **no credentials need to be configured**. The SDK auto-discovers them via the instance metadata service.

```powershell
# Sanity check (the role's identity should appear)
aws sts get-caller-identity
```

If this fails, the role is missing or IMDS is blocked. Don't fall back to long-lived keys without escalation.

---

## 5. Method C — `aws configure` (long-lived access keys)

Use only when SSO/IAM-roles are not available. Keys must be rotated regularly.

### Setup

```powershell
aws configure --profile <profile-name>
```

Prompts:
- **AWS Access Key ID**: `<paste-from-IAM-console>`
- **AWS Secret Access Key**: `<paste-from-IAM-console>`
- **Default region name**: `<region>` (e.g. `us-east-1`)
- **Default output format**: `json`

Stored in `%USERPROFILE%\.aws\credentials` (plaintext but ACL-protected). NEVER copy this file into the repo, into chat, or onto shared drives.

### Use

```powershell
aws sts get-caller-identity --profile <profile-name>
$env:AWS_PROFILE = "<profile-name>"
```

### Rotation reminder

```powershell
# List your access keys
aws iam list-access-keys --profile <profile-name>

# Create a new key, swap it in (aws configure), then delete the old one
aws iam create-access-key --profile <profile-name>
aws iam delete-access-key --access-key-id <old-key-id> --profile <profile-name>
```

Rotate every 90 days minimum, or whenever a key may have been exposed.

---

## 6. Method D — Environment variables (CI / one-shot sessions)

```powershell
# Persist for current user (survives reboots)
setx AWS_ACCESS_KEY_ID     "<key-id>"
setx AWS_SECRET_ACCESS_KEY "<secret>"
setx AWS_DEFAULT_REGION    "<region>"

# Or set for the current shell only
$env:AWS_ACCESS_KEY_ID     = "<key-id>"
$env:AWS_SECRET_ACCESS_KEY = "<secret>"
$env:AWS_DEFAULT_REGION    = "<region>"

# Sanity check
aws sts get-caller-identity
```

For temporary STS credentials (e.g. assumed role, MFA), also set `AWS_SESSION_TOKEN`.

Prefer SSO over `setx` — `setx` writes to the registry in plaintext.

---

## 7. Profile management

```powershell
# List configured profiles
aws configure list-profiles

# Inspect a profile (NEVER pipe to a file you might commit)
aws configure list --profile <profile-name>

# Switch the default profile for this shell
$env:AWS_PROFILE = "<profile-name>"

# Use a specific profile per command (overrides AWS_PROFILE)
aws s3 ls --profile <profile-name>
```

Config files location:
- `%USERPROFILE%\.aws\config` — non-secret profile metadata (SSO URL, region, role ARNs)
- `%USERPROFILE%\.aws\credentials` — long-lived keys (only if Method C used)
- `%USERPROFILE%\.aws\sso\cache\` — SSO tokens (auto-managed)

---

## 8. Common commands

```powershell
# Identity
aws sts get-caller-identity

# S3
aws s3 ls
aws s3 ls s3://<bucket>
aws s3 cp <local-file> s3://<bucket>/<key>
aws s3 sync <local-dir> s3://<bucket>/<prefix>

# EC2
aws ec2 describe-instances
aws ec2 describe-instances --filters "Name=tag:Name,Values=<tag-value>"

# IAM (read your own permissions)
aws iam get-user
aws iam list-attached-user-policies --user-name <username>

# CloudWatch logs
aws logs tail /aws/lambda/<function-name> --follow

# Switch region per command
aws s3 ls --region <other-region>
```

---

## 9. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Unable to locate credentials` | No active profile | `aws sso login --profile <name>` or set `AWS_PROFILE` |
| `An error occurred (ExpiredToken)` | SSO session expired | `aws sso login --profile <name>` |
| `An error occurred (AccessDenied)` | Role/policy missing the action | Check IAM policy; do not paper over with broader perms |
| `Could not connect to endpoint` | Wrong region or no network | Verify `--region` and connectivity |
| `aws: command not found` | CLI not on PATH | Restart shell; reinstall via winget |
| Wrong account being used | `AWS_PROFILE` set to a stale value | `Remove-Item env:AWS_PROFILE; aws sts get-caller-identity` |

---

## 10. What never to do

- ❌ **Never** commit `.aws/credentials`, access keys, or SSO tokens to git.
- ❌ **Never** paste keys into Slack, email, screenshots, or markdown files.
- ❌ **Never** hardcode keys in Python/PowerShell scripts. Read from env or `aws configure`.
- ❌ **Never** keep deactivated employees' keys around — rotate immediately on offboarding.
- ❌ **Never** use the root account access keys. Create an IAM user / SSO identity.
- ❌ **Never** disable MFA on production accounts.

If a key has been exposed: **rotate it immediately** (`aws iam create-access-key` → swap → `delete-access-key`) and audit CloudTrail for unauthorized use.

---

## 11. Pre-committed scanning (defense in depth)

Add a pre-commit hook that scans for AWS credentials patterns:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/Yelp/detect-secrets
    rev: v1.5.0
    hooks:
      - id: detect-secrets
        args: ['--baseline', '.secrets.baseline']
```

Or use `git-secrets` (`git secrets --register-aws`) for AWS-specific patterns.

---

## 12. Where to look for more

- AWS CLI v2 docs: https://docs.aws.amazon.com/cli/latest/userguide/
- IAM Identity Center: https://docs.aws.amazon.com/singlesignon/
- Best practices for keys: https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html
