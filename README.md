# Acta Automation Approval Workflow

This project automates the generation and approval of Acta documents, integrating AWS Lambda, DynamoDB, SES, S3, and API Gateway.

## Purpose
- Automatically extract client emails and Acta metadata.
- Generate Acta PDF documents.
- Send approval requests via email with attached Actas.
- Track approvals and rejections through API Gateway.

## Components

| Service | Purpose |
|---------|---------|
| AWS Lambda | Hosts `sendApprovalEmail` and `handleApprovalCallback` functions |
| AWS API Gateway | Handles `/approve` callback endpoint |
| AWS DynamoDB | Stores Acta metadata including client email mapping |
| AWS S3 | Stores generated Acta PDF documents |
| AWS SES | Sends Acta approval emails to clients |

## Deployment

### Environment Variables
- `AWS_REGION`: Deployment AWS region (e.g., `us-east-2`)
- `AWS_ACCOUNT_ID`: AWS Account ID
- `S3_BUCKET_NAME`: S3 bucket where Acta PDFs are stored
- `DYNAMODB_TABLE_NAME`: DynamoDB table storing Acta metadata
- `EMAIL_SOURCE`: Verified SES email address used to send emails
- `DOMAIN`: API Gateway domain for approval links

### Lambda Functions
- `sendApprovalEmail`:
  - Extracts client email from DynamoDB (`title == 'Client_Email'`)
  - Fetches Acta PDF from S3
  - Sends approval request email with PDF attached
  - Includes branded HTML with Approve/Reject buttons

- `handleApprovalCallback`:
  - Updates Acta status in DynamoDB based on approval/rejection

### Branded Email Flow
- HTML includes Ikusi logo and styling.
- Approve and Reject buttons redirect to API Gateway URLs.
- Additional comment prompt provided post-click (in UI, not email).

Sample HTML Preview:
```
<html>
  <body style="font-family:Verdana, sans-serif; color:#333;">
    <div style="padding:20px; border:1px solid #ccc; max-width:600px;">
      <img src="https://ikusi.com/branding/logo.png" alt="Ikusi" style="max-width:150px; margin-bottom:10px;">
      <h2 style="color:#4AC795;">Acta Approval Request</h2>
      <p>
        Please review the attached Acta document.
        You may approve or reject this Acta using the buttons below.
      </p>
      <div style="margin-top:20px;">
        <a href="{{ approve_url }}" style="padding:10px 20px; background:#4AC795; color:#fff; text-decoration:none; border-radius:4px;">✔️ Approve</a>
        <a href="{{ reject_url }}" style="padding:10px 20px; background:#E74C3C; color:#fff; text-decoration:none; border-radius:4px; margin-left:10px;">✖️ Reject</a>
      </div>
      <p style="margin-top:30px; font-size:13px; color:#999;">
        If you would like to provide additional comments or context, please include them via the approval interface after clicking.
      </p>
    </div>
  </body>
</html>
```

### Testing

#### Lambda Test Event (sendApprovalEmail)
Use this JSON to manually test from the Lambda Console:
```json
{
  "acta_id": "your-project-id"
}
```
Replace `your-project-id` with a valid `project_id` stored in DynamoDB that includes:
- A card titled `Client_Email`
- A `comments` array with a verified SES email
- An `s3_pdf_path` value pointing to the correct Acta document in S3

### Known Considerations
- SES in Sandbox mode requires recipient email verification.
- Lambda must include `config/email_map.json` inside ZIP package.
- Approve/Reject feedback UI coming in Module 2 (Portal).

## Future Enhancements
- Add retry logic for SES email failures.
- Add auto-expire for pending approvals after X days.
- Expand approval flows into a client-facing UI portal.

---

Built with focus on brand integrity, operational excellence, and production-grade scalability.

---

✨ Project sponsored and maintained by CVDex Tech Solutions, Strategic Developer: AIGOR.
