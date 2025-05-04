# CVDex – ProjectPlace Acta Automation Platform

> End-to-end serverless workflow that **generates**, **emails**, and **captures approvals** for Acta documents pulled from ProjectPlace.

---

## 📁 Repository Layout

| Path | Purpose |
|------|---------|
| `approval/` | Lambda business logic |
| ├── `sendApprovalEmail.py` |  ➜ Sends approve / reject email with PDF attachment |
| └── `handleApprovalCallback.py` |  ➜ Processes `/approve?token=…` clicks |
| `scripts/` | Deployment helpers |
| ├── `deploy_metadata_enricher.py` |  ➜ Zip & deploy `projectMetadataEnricher` Lambda |
| ├── `deploy_approval_workflow.py` |  ➜ Deploy both email + callback Lambdas **plus** API Gateway |
| └── `deploy_send_approval_email.py` |  ➜ (Standalone) redeploy email Lambda |
| `deployment_zips/` | Auto-generated build artifacts (git-ignored) |
| `.github/workflows/` | CI/CD pipelines |
| ├── `deploy_metadata_enricher.yml` |
| └── `deploy_approval_workflow.yml` |
| `README.md` | You’re reading it |

> **Important:** All build artifacts are created on-the-fly by workflows—no manual zips committed to source.

---

## 🌐 High-Level Architecture

ProjectPlace → Extractor Lambda │ └── uploads DOCX/PDF to S3 ▼ projectMetadataEnricher Lambda │ └── adds client/PM emails, approval_token row in DynamoDB ▼ sendApprovalEmail Lambda │ └── SES email (HTML) ➜ client ▼ Client Clicks /approve?token=XYZ │ API Gateway ↦ handleApprovalCallback Lambda │ └── updates approval_status in DynamoDB ▼ DynamoDB status = approved / rejected


*A live systems diagram is tracked in the project BRD canvas.*

---

## 🚀 Quick-Start (CI/CD only)

1. **Clone** the repo and push updates to `main`.
2. Add GitHub **Secrets**:  
   * `AWS_ACCESS_KEY_ID`  
   * `AWS_SECRET_ACCESS_KEY`  
   * `AWS_ACCOUNT_ID`  
   * *(Optional override)* `S3_BUCKET_NAME` (defaults baked into workflow)
3. Verify in **SES (sandbox)**  
   * Sender: `AutomationSolutionsCenter@cvdexinfo.com`  
   * At least one recipient inbox (test)
4. In GitHub → **Actions**  
   1. Run **“Deploy projectMetadataEnricher”**  
   2. Run **“Deploy Acta Approval Workflow”**

Both workflows include smoke tests:
* Enricher: invokes Lambda with `{}` and prints result.
* Approval: invokes `sendApprovalEmail` with a dummy `acta_id`; job fails if SES reply ≠ 200.

---

## 🔑 Environment Variables

| Name | Set By | Used In | Example |
|------|--------|--------|---------|
| `AWS_REGION` | Workflow | All Lambdas | `us-east-2` |
| `AWS_ACCOUNT_ID` | Secret | Deploy scripts | `123456789012` |
| `EMAIL_SOURCE` | Workflow | `sendApprovalEmail` | `AutomationSolutionsCenter@cvdexinfo.com` |
| `ACTA_API_ID` | Auto-captured | `sendApprovalEmail` | `4r0pt34gx4` |
| `DYNAMODB_TABLE_NAME` | Workflow | All Lambdas | `ProjectPlace_DataExtrator_landing_table_v3` |
| `S3_BUCKET_NAME` | Workflow | `sendApprovalEmail` | `projectplace-dv-2025-x9a7b` |

---

## 🧪 Manual Testing

```bash
# Trigger email Lambda manually (CLI)
aws lambda invoke \
  --function-name sendApprovalEmail \
  --payload '{"acta_id":"100000000000000"}' \
  --cli-binary-format raw-in-base64-out \
  out.json --region us-east-2

# Simulate approval click
curl "https://<ACTA_API_ID>.execute-api.us-east-2.amazonaws.com/prod/approve?token=<TOKEN>&status=approved"

Check DynamoDB record for approval_status = approved.
