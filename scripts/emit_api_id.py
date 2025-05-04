# scripts/emit_api_id.py
import scripts.deploy_approval_workflow as d

d.main()
print(d.last_api_id)
