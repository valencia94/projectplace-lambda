name: Deploy Metadata Enricher

on:
  workflow_dispatch:

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: 3.9

      - name: Install Boto3
        run: pip install boto3

      - name: Run deploy script
        env:
          AWS_REGION: ${{ secrets.AWS_REGION }}
          AWS_ACCOUNT_ID: ${{ secrets.AWS_ACCOUNT_ID }}
        run: python scripts/deploy_metadata_enricher.py
