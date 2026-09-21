.PHONY: test deploy destroy seed trigger clean

ENV ?= dev
AWS_REGION ?= us-west-2
STACK_NAME ?= prompt-memory-$(ENV)

# Run unit tests
test:
	python -m pytest tests/ -v --tb=short

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
	python scripts/seed_corrections.py --env $(ENV) --region $(AWS_REGION)

# Upload a sample document to trigger extraction
trigger:
	python scripts/trigger_extraction.py --env $(ENV) --region $(AWS_REGION)

# Destroy all resources
destroy:
	sam delete --stack-name $(STACK_NAME) --region $(AWS_REGION) --no-prompts

# Remove build artifacts
clean:
	rm -rf .aws-sam/ __pycache__ .pytest_cache
