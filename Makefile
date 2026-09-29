.PHONY: test build deploy destroy seed trigger verify empty-buckets clean

ENV ?= dev
AWS_REGION ?= us-west-2
STACK_NAME ?= sample-prompt-correction-memory-$(ENV)
# Python interpreter (override with `make PYTHON=python seed` if needed).
PYTHON ?= python3

# Run unit tests
test:
	$(PYTHON) -m pytest tests/ -v --tb=short

# Deploy with SAM
build:
	sam build -t infrastructure/template.yaml

deploy: build
	sam deploy \
		--stack-name $(STACK_NAME) \
		--parameter-overrides Environment=$(ENV) \
		--capabilities CAPABILITY_IAM \
		--resolve-s3 \
		--region $(AWS_REGION)

# Seed correction log with sample corrections
seed:
	$(PYTHON) scripts/seed_corrections.py --env $(ENV) --region $(AWS_REGION)

# Upload a sample document to trigger extraction
trigger:
	$(PYTHON) scripts/trigger_extraction.py --env $(ENV) --region $(AWS_REGION)

# Read back extraction results to verify the deployed pipeline worked
verify:
	$(PYTHON) scripts/verify_extraction.py --env $(ENV) --region $(AWS_REGION)

# Empty the versioned S3 buckets so the stack can be deleted cleanly.
# (Versioned buckets block stack deletion until all object versions are gone.)
empty-buckets:
	@ACCOUNT=$$(aws sts get-caller-identity --query Account --output text --region $(AWS_REGION)); \
	for suffix in docs logs; do \
		BUCKET=sample-prompt-correction-memory-$$suffix-$$ACCOUNT-$(ENV); \
		echo "Emptying $$BUCKET ..."; \
		aws s3api delete-objects --bucket $$BUCKET --region $(AWS_REGION) \
			--delete "$$(aws s3api list-object-versions --bucket $$BUCKET --region $(AWS_REGION) \
				--query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}' --output json)" 2>/dev/null || true; \
		aws s3api delete-objects --bucket $$BUCKET --region $(AWS_REGION) \
			--delete "$$(aws s3api list-object-versions --bucket $$BUCKET --region $(AWS_REGION) \
				--query '{Objects: DeleteMarkers[].{Key:Key,VersionId:VersionId}}' --output json)" 2>/dev/null || true; \
	done

# Destroy all resources (empties versioned buckets first)
destroy: empty-buckets
	sam delete --stack-name $(STACK_NAME) --region $(AWS_REGION) --no-prompts

# Remove build artifacts
clean:
	rm -rf .aws-sam/ __pycache__ .pytest_cache
