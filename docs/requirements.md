My idea centers around an aws org with a logarchive account. I want to create a s3 bucket in us-east-1 which indicates it is the
home for all application logging.

Application Logging includes and is not limited Application

1. EKS Container Logging
2. Lambda Logging
3. ECS logging
4. Baremetal apps running on EC2 logging

For EKS I have rough contours of partionining logic which I will include in the future. Look at ../../cluster-cauldron/apps/app-of-apps/children/splunk-otel/README.md and the contents of this kube-prometheus-stack
I plan to spin up splunk otel which will scrape logs and then further ship it to fluent bit which will then finally ship it to this S3 bucket.

Read the @cribl-s3.md which tells you how cribl is going to ingest the logs and further ship it to on-prem splunk.

I want to build a layout where the bucket its lifecycle policy etc are all created in the logarchive account. Further I want to
create IAM Roles/Polcies which will be pushed into the workload accounts which EKS Pod Identity or EC2 IAM Role etc will take on which
will allow stuff to be able to write to it.

Also these can only write in and cannot delete objects. Hopefully the apps will not collide and if it does it should get an error
if names are being reused.

There could also be a case where I can have a kube-prometheus-stack which has loki which may be able to consume logs from this bucket if there is a need for it
