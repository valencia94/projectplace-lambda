## ✅ Updated `README.md` Summary for Acta Automation Platform (CVDex)

### 📁 Repository Layout (Updated)
| Path | Purpose |
|------|---------|
| `approval/` | Lambda business logic |
| ├── `sendApprovalEmail.py` |  ➜ Sends approve/reject email with PDF attachment |
| ├── `handleApprovalCallback.py` |  ➜ Processes `/approve?token=…` clicks |
| `scripts/` | Deployment helpers |
| ├── `deploy_metadata_enricher.py` |  ➜ Deploy `projectMetadataEnricher` Lambda |
| ├── `deploy_approval_workflow.py` |  ➜ Deploy both email + callback Lambdas & API Gateway |
| └── `deploy_send_approval_email.py` |  ➜ Redeploy `sendApprovalEmail` Lambda |
| `.github/workflows/` | CI/CD pipelines |
| ├── `deploy_metadata_enricher.yml` |
| ├── `deploy_approval_workflow.yml` |
| └── `deploy_callback_lambda.yml` |  ➜ Deploy `handleApprovalCallback` Lambda + flush API Gateway ✔ |

> 🔧 Build artifacts (ZIPs) are created on-the-fly via CI workflows – nothing is committed manually.

---

### 🚀 Quick-Start (CI/CD Deployment)
1. Clone repo, push updates to `main`.
2. Ensure GitHub **Secrets** are set:
   - `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_ACCOUNT_ID`
   - `ACTA_API_ID`, `API_STAGE`, `EMAIL_SOURCE`, etc.
3. In GitHub → **Actions** tab:
   1. Run **"Deploy projectMetadataEnricher"**
   2. Run **"Deploy Acta Approval Workflow"**
   3. Run **"Deploy Callback Lambda + API Flush"** ✅

---

### 🧪 Manual Testing
```bash
# Manually invoke email Lambda
aws lambda invoke \
  --function-name sendApprovalEmail \
  --payload '{"acta_id":"100000000000000"}' \
  --cli-binary-format raw-in-base64-out \
  out.json --region us-east-2

# Simulate approval click with optional comment
domain="https://<ACTA_API_ID>.execute-api.us-east-2.amazonaws.com/prod"
curl "$domain/approve?token=<TOKEN>&status=approved&comment=Looks%20great"
