#!/usr/bin/env bash
# One command: deploy the stack, then publish the static site.
# Uses the default AWS profile, us-east-1 (see samconfig.toml).
set -euo pipefail
cd "$(dirname "$0")"
# The budget-alert / reminder-sender email lives in an untracked .env (ALERT_EMAIL=...), never in git.
[ -f .env ] && set -a && . ./.env && set +a
: "${ALERT_EMAIL:?Set ALERT_EMAIL (a verified SES identity that also gets the \$5 budget alert), e.g. in .env}"
ALARM_EMAIL="${ALARM_EMAIL:-$ALERT_EMAIL}"   # CloudWatch alarm emails (NoEcho parameter)
sam build
sam deploy --parameter-overrides "AlertEmail=$ALERT_EMAIL BudgetLimitUsd=5 AlarmEmail=$ALARM_EMAIL"
out() { aws cloudformation describe-stacks --stack-name thousand-worriers --region us-east-1 \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
BUCKET=$(out BucketName); DIST=$(out DistributionId); URL=$(out SiteUrl)
aws s3 sync site/ "s3://$BUCKET/site/" --delete --region us-east-1 --exclude index.html
# the page itself must always revalidate so new ?v= asset versions take effect immediately
aws s3 cp site/index.html "s3://$BUCKET/site/index.html" --region us-east-1 --content-type "text/html; charset=utf-8" --cache-control "no-cache"
aws cloudfront create-invalidation --distribution-id "$DIST" --paths "/*" --query Invalidation.Id --output text
echo "Live at: $URL"
