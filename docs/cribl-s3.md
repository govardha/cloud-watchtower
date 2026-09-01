# Amazon S3 Source

> For the complete documentation index, see [llms.txt](/llms.txt).

Cribl Stream supports receiving data from [Amazon S3](https://aws.amazon.com/s3/) buckets. The Amazon S3 Source can receive data using either of these methods:

- [S3 Bucket event notifications](https://docs.aws.amazon.com/AmazonS3/latest/dev/NotificationHowTo.html) through Amazon Simple Queue Service (SQS)
- [Amazon Security Lake event subscriptions](https://docs.aws.amazon.com/security-lake/latest/userguide/subscriber-management.html)

These two notifications use the same method of sending data objects to a queue, but the underlying data format from these two methods differ. Cribl Stream supports both data structures.

> Type: **Pull** | TLS Support: **YES** (secure API) | Event Breaker Support: **YES**
>
> Cribl Stream running on Linux (only) can use this Source to read Parquet files, identified by a `.parquet`, `.parq`, or `.pqt` filename extension.
>
> For configuration examples, see our [Amazon S3 Better Practices](/stream/usecase-s3-better-practices/) and [Using S3 Storage and Replay](/stream/usecase-replay-s3/) guides.

{.box .info}

## S3 Setup Strategy

The source S3 bucket must be configured to send `s3:ObjectCreated:*` events to an SQS queue, either directly (easiest) or via SNS (Amazon Simple Notification Service). See the [event notification configuration guidelines](#configure_s3) below.

SQS messages will be deleted after they're read, unless an error occurs, in which case Cribl Stream will retry. This means that although Cribl Stream will ignore files not matching the **Filename Filter**, their SQS events/notifications will still be read, and then deleted from the queue (along with those from files that match).

These ignored files will no longer be available to other S3 Sources targeting the same SQS queue. If you still need to process these files, we suggest one of these alternatives:

- Using a different, dedicated SQS queue. (Preferred and recommended.)

- Applying a broad filter on a single Source, and then using pre-processing Pipelines an/or Route filters for further processing.

### Compression

Cribl Stream can ingest compressed S3 files if they meet one of the following conditions:

- Compressed with the `x-gzip` MIME type.
- Ends with the `.gz`, `.tgz`, `.tar.gz`, `.tgz`, or `.tar` extension.
- Can be uncompressed using the `zlib.gunzip` algorithm.

### Storage Class Compatibility

Cribl Stream does **not** support data preview, collection, or replay
from S3 Glacier or S3 Deep Glacier storage classes, whose stated retrieval lags
(variously minutes to 48 hours) cannot guarantee data availability when the
Collector needs it.

Cribl Stream **does** support data preview, collection, and replay from S3 Glacier
Instant Retrieval when you're using the S3 Intelligent-Tiering storage class.

## Configure Cribl Stream to Receive Data from Amazon S3

1. On the top bar, select **Products**, and then select **Cribl Stream**. Under **Worker Groups**, select a Worker Group. Next, you have two options:
   - To configure via [QuickConnect](quickconnect), navigate to **Routing** > **QuickConnect**. Select **Add Source** and select the Source you want from the list, choosing either **Select Existing** or **Add New**.
   - To configure via the [Routes](routes), select **Data** > **Sources**. Select the Source you want. Next, select **Add Source**.
1. In the **New Source** modal, configure the following under **General Settings**:
   - **Input ID**: Enter a unique name to identify this S3 Source definition. If you clone this Source, Cribl Stream will add `-CLONE` to the original **Input ID**.
   - **Description**: Optionally, enter a description.
   - **Queue**: The name, URL, or Amazon Resource Name (ARN) of the SQS queue to read events from. When specifying a non-AWS URL, you must use the format: `{url}/<queueName>`. (For example: `https://host:port/<queueName>`.) This value must be a JavaScript expression (which can evaluate to a constant), enclosed in single quotes, double quotes, or backticks.
1. Next, you can configure the following **Optional Settings**:
   - **Filename filter**: Regex matching file names to download and process. Defaults to `.*`, to match all characters. This regex will be evaluated against the S3 key's full path.
   - **Region**: AWS Region where the S3 bucket and SQS queue are located. Required, unless the **Queue** entry is a URL or ARN that includes a Region.
   - **Tags**: Optionally, add tags that you can use to filter and group Sources in Cribl Stream's UI. These tags aren't added to processed events. Use a tab or hard return between (arbitrary) tag names.
1. In the **Authentication** settings, select one of the following options from the menu:

   | Option | Description                                                                                                                                                                                                                                                                                                                                                                                           |
   | ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
   | Auto   | This default option uses the [AWS SDK for JavaScript](https://docs.aws.amazon.com/sdk-for-javascript/v3/developer-guide/setting-credentials-node.html) to check for credentials in a specific order by priority. It first checks for IAM Roles for Amazon EC2, then a shared credentials file, then environmental variables, and so forth. See [Authentication Settings](#auth) for more information. |
   | Manual | If not running on AWS, select this option to enter a static set of user-associated IAM credentials (your access key and secret key) directly or by reference. This is useful for Workers not in an AWS VPC, such as those running a private cloud. See [Authentication Settings](#auth) for more information.                                                                                         |
   | Secret | If not running on AWS, select this option to supply a stored secret that references an AWS access key and secret key. See [Authentication Settings](#auth) for more information.                                                                                                                                                                                                                      |

1. If needed, configure the **AssumeRole** settings. You can use independent role chains for S3 and SQS to support cross-account IAM patterns where each service must use different credentials. See [Assume Role Settings](#assume) for more information.
1. Optionally, refer to the following sections for the remaining settings:
   - [Processing Settings](#processing)
   - [Advanced Settings](#advanced-tab)
   - [Connected Destinations](#connected)
1. Select **Save**, then **Commit & Deploy**.
1. Verify that data is flowing correctly to Cribl Stream by viewing the [**Live Data**](sources#capture-source-data) feed from the Source.

### Authentication {#auth}

Use the **Authentication method** drop-down to select an AWS authentication method.

**Auto**: This default option uses the [AWS SDK for JavaScript](https://docs.aws.amazon.com/sdk-for-javascript/v3/developer-guide/setting-credentials-node.html) to automatically obtain credentials in the following order of attempts:

1. **Environment Variables**: Loaded from environment variables `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`.
1. **IAM Identity Center (SSO)**: Loaded from AWS IAM Identity Center configuration.
1. **Shared Credentials File**: Loaded from the shared credentials file (`~/.aws/credentials`).
1. **IAM Roles for Amazon EC2/ECS**: Loaded from AWS Identity and Access Management (IAM) roles attached to an EC2 instance or ECS container.
1. **JSON File on Disk**: Loaded from a JSON file on disk.
1. **Other Credential-Provider Classes**: Other credential-provider classes provided by the AWS SDK for JavaScript.

The `Auto` method works both when running on AWS and in other environments where the necessary credentials are available through one of the above methods.

> ##### SSO Providers
>
> When using the auto authentication method, you can leverage SSO providers like [SAML](/iam/usecase-sso-saml#temp-access) and [Okta](/iam/usecase-sso-okta#temp-access) to issue temporary credentials. These credentials should be set in the environment variables `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`. The AWS SDK will then use these environment variables to authenticate.
> {.box .info}

**Manual**: If not running on AWS, you can select this option to enter a static set of user-associated IAM credentials (your access key and secret key) directly or by reference. This is useful for Workers not in an AWS VPC, such as those running a private cloud. The **Manual** option exposes these corresponding additional fields:

- **Access key**: Enter your AWS access key. If not present, will fall back to the `env.AWS_ACCESS_KEY_ID` environment variable, or to the metadata endpoint for IAM role credentials.

- **Secret key**: Enter your AWS secret key. If not present, will fall back to the `env.AWS_SECRET_ACCESS_KEY` environment variable, or to the metadata endpoint for IAM credentials.

The values for **Access key** and **Secret key** can be a constant or a JavaScript expression (such as `${C.env.MY_VAR}`). Enclose values containing special characters (like, `/`) or using environment variables in single quotes or backticks.

**Secret**: If not running on AWS, you can select this option to supply a stored secret that references an AWS access key and secret key. The **Secret** option exposes this additional field:

- **Secret key pair**: Use the drop-down to select a secret key pair that you've [configured](securing-kms-config) in Cribl Stream's internal secrets manager or (if enabled) an external KMS. Follow the **Create** link if you need to configure a key pair.

### Assume Role {#assume}

When using **Assume Role** to access resources in a different region than Cribl Stream, you can target the [AWS Security Token Service](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_temp.html) (STS) endpoint specific to that region by using the `CRIBL_AWS_STS_REGION` environment variable on your Worker Node. To specify a custom STS endpoint, set the `CRIBL_AWS_STS_ENDPOINT` environment variable. If both are set, `CRIBL_AWS_STS_ENDPOINT` takes precedence. Setting an invalid region or endpoint results in a fallback to the global STS endpoint.

The S3 Source supports independent, multi-hop AssumeRole chains for S3 and SQS. This lets you configure stricter cross-account IAM patterns where, for example, SQS must be read with the EC2 instance role while S3 object reads require chaining through roles in a separate account.

Each chain is an ordered list of STS AssumeRole hops, where each hop uses the previous hop's resolved credentials. A maximum of 3 hops is supported per chain. Leave a chain empty to disable role assumption for that service.

#### AssumeRole chain for Amazon S3

Select **Add S3** to add a hop to the S3 AssumeRole chain. Each hop has the following fields:

- **AssumeRole ARN**: The Amazon Resource Name (ARN) of the role to assume for this hop. When you run this Source on Cribl.Cloud Worker Groups, configure your AWS role's trust policy to allow the Worker role ARN to assume it. To get the role ARN for Workers in a Worker Group, see [Worker Group Details](workspaces#group-details).

- **External ID**: The External ID to use when assuming the role for this hop.

- **Duration (seconds)**: Duration of the assumed role's session, in seconds. Minimum is 900 (15 minutes). Maximum is 43200 (12 hours). Defaults to 3600 (1 hour).

- **Session name**: Optional STS session name. Must be 2-64 characters using only alphanumeric characters and the symbols `+`, `=`, `,`, `.`, `@`, `-`. Defaults to a generated value.

#### AssumeRole chain for Amazon SQS

Select **Add SQS** to add a hop to the SQS AssumeRole chain. Each hop exposes the same fields as the S3 chain.

**AWS account ID**: The SQS queue owner's AWS account ID. Leave empty if the SQS queue is in the same AWS account.

#### Override credentials for S3

**Override credentials for S3**: Toggle on to use independent base credentials for the S3 client. When toggled off (default), S3 inherits the top-level credentials used by SQS.

When toggled on, the following **S3 credentials** settings become available:

- **Authentication method**: Select **Auto**, **Manual**, or **Secret** to supply base credentials for the S3 client. These options work the same as the top-level [Authentication](#auth) settings, but apply only to S3 requests.

### Processing Settings {#processing}

#### Custom Command

In this section, you can pass the data from this input to an external command for processing, before the data continues downstream.

**Enabled**: Default is toggled off. Toggle on to enable the custom command.

**Command**: Enter the command that will consume the data (via `stdin`) and will process its output (via `stdout`).

**Arguments**: Select **Add Argument** to add each argument to the command. You can drag arguments vertically to resequence them.

#### Event Breakers

This section defines event breaking rulesets that will be applied, in order.

**Event Breaker rulesets**: A list of event breaking rulesets that will be applied to the input data stream before the data is sent through the Routes. Defaults to `System Default Rule`.

**Event Breaker buffer timeout**: How long (in milliseconds) the Event Breaker will wait for new data to be sent to a specific channel, before flushing out the data stream, as-is, to the Routes. Minimum `10` ms, default `10000` (10 sec), maximum `43200000` (12 hours).

#### Fields

In this section, you can define new fields or modify existing ones using JavaScript expressions, similar to the [Eval](eval-function) function.

- The **Field Name** can either be a new field (unique within the event) or an existing field name to modify its value.
- The **Value** is a JavaScript expression (enclosed in quotes or backticks) to compute the field's value (can be a constant). Select this field's advanced mode icon (far right) if you'd like to open a modal where you can work with sample data and iterate on results.

This flexibility means you can:

- Add new fields to enrich the event.
- Modify existing fields by overwriting their values.
- Compute logic or transformations using JavaScript expressions.

**Include notification metadata**: Toggle on to add the S3 event notification payload to a `__sqsMetadata` field on every event from the corresponding S3 file.

This metadata is useful for several purposes. You can measure processing latency by comparing the object's `eventTime` to the event's `_time`, or implement region-based routing using the `awsRegion` field in a Pipeline. The full payload also serves as a definitive record for auditing the origin of the data.

#### Pre-Processing

In this section's **Pipeline** drop-down list, you can select a single existing [Pipeline](pipelines) or [Pack](packs) to process data from this input before the data is sent through the Routes.

### Advanced Settings {#advanced-tab}

#### Checkpoint Settings

**Enable checkpointing**: When toggled off (default) Cribl Stream will always start processing a file from the beginning. Toggle on if, after an error or restart occurs, you want Cribl Stream to resume processing a given file from the point where it was interrupted, as explained [below](#checkpointing). When turned on, the following additional setting becomes available:

- **Retries**: The number of times you want Cribl Stream to retry processing a file after an error or restart occurs. If **Skip file on error** is enabled, this setting is ignored.

#### Other Settings

**Endpoint**: S3 service endpoint. If empty, defaults to AWS's Region-specific endpoint. Otherwise, used to point to an S3-compatible endpoint. To access the AWS S3 endpoints, use the path-style URL format. You don't need to specify the bucket name in the URL, because Cribl Stream will automatically add it to the URL path. For details, see AWS' [Path-Style Requests](https://docs.aws.amazon.com/AmazonS3/latest/userguide/VirtualHosting.html#path-style-access) topic.

**Signature version**: Signature version to use for signing SQS requests. Defaults to `v4`.

**Message limit**: The maximum number of messages that SQS should return in a poll request. Amazon SQS never returns more messages than this value. (However, fewer messages might be returned.) Acceptable values: 1 to 10. Defaults to `1`.

**Visibility timeout seconds**: The duration (in seconds) that the received messages are hidden from subsequent retrieve requests, after being retrieved by a ReceiveMessage request. Defaults to `600`.

> Cribl Stream will automatically extend this timeout until the initial request's files have been processed - notably, in the case of large files that require additional processing time.

{.box .info}

**Number of receivers**: The number of receiver processes to run. The higher the number, the better the throughput, at the expense of CPU overhead. Defaults to `1`.

**Poll timeout (secs)**: How long to wait for events before polling again. Minimum `1` second; default `10`; maximum `20`. Short durations increase the number - and thus the cost - of requests sent to AWS. (The UI will show a warning for intervals shorter than `5` seconds.) Long durations increase the time the Source takes to react to configuration changes and system restarts.

**Socket timeout**: Socket inactivity timeout (in seconds). Increase this value if retrievals time out during backpressure. Defaults to `300` seconds.

**Parquet chunk size limit (MB)**: Maximum size for each Parquet chunk. Defaults to `5` MB. Valid range is `1` to `100` MB. Cribl Stream stores chunks in the location specified by the `CRIBL_TMP_DIR` [environment variable](environment-variables#cribl_tmp_dir). It removes the chunks immediately after reading them.

**Parquet chunk download timeout (seconds)**: The maximum time to wait for a Parquet file's chunk to be downloaded. If a required chunk cannot be downloaded within this time limit, processing will end. Defaults to `600` seconds. Valid range is `1` second to `3600` seconds (1 hour).

**Skip file on error**: Toggle on to skip files that trigger a processing error (for example, corrupted files). Default is toggled off, which enables retries after a processing error.

**Reuse connections**: Whether to reuse connections between requests. Toggling on (default) can improve performance.

**Reject unauthorized certificates**: Whether to accept certificates that cannot be verified against a valid Certificate Authority (for example, self-signed certificates). Defaults to toggled on.

**Encoding**: Character encoding to use when parsing ingested data. If not set, Cribl Stream will default to UTF-8 but might incorrectly interpret multi-byte characters. This option is ignored for Parquet files. UTF-16LE and Latin-1 are also supported.

**Tag after processing**: Toggle on to tag S3 objects once processed, allowing you to leverage tools like [S3 Lifecycle](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lifecycle-mgmt.html) to automatically delete, archive, or further process only those objects confirmed as ingested by Cribl. This requires the `s3:GetObjectTagging` and `s3:PutObjectTagging` S3 permissions.

**Tag key** and **Tag value** support the Cribl [expression syntax](cribl-reference). These expressions are evaluated once when the S3 Source is initialized, not for each file processed. The resulting static key and value are then applied to all processed objects for the entire lifespan of the Source. See Amazon's documentation on how to [categorize your storage using tags](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-tagging.html) for details and requirements.

- **Tag key**: The key for the S3 object tag. The key identifies the tag in AWS and must comply with S3 tag key requirements (maximum 128 Unicode characters, no spaces, case-sensitive).
- **Tag value**: The value for the S3 object tag. The value is paired with the key and must comply with S3 tag value requirements (maximum 256 Unicode characters, case-sensitive).

**Environment**: If you're using GitOps, optionally use this field to specify a single Git branch on which to enable this configuration. If empty, the config will be enabled everywhere.

#### Checkpointing {#checkpointing}

When Workers restart, checkpointing can prevent the Source from losing data or collecting duplicate events. Among the situations where this works well:

- When Cribl Stream pushes a new configuration: the Leader pushes the configuration to the Workers, and restarts them.
- When a Worker has gotten into a bad state, and you want to [restart](configuration-files#restart) it.

Checkpointing allows Cribl Stream to resume processing a file at the point where an error or restart occurred, minimizing the number of duplicate events sent downstream in the event of an interruption. To accomplish this, Cribl Stream keeps track of how many events have been processed for a given file. Resuming processing after an interruption, the Source skips the first _N_ events it has seen and starts processing from event _N_+1.

The Source manages checkpoint information in memory, but writes it out to disk periodically (once per second, and on Worker shutdown) so that the information can survive a Worker restart. Cribl Stream keeps checkpoint information at the Worker Process level and does not share it between Worker Processes or Worker Nodes.

Checkpointing is a feature to keep in mind when you do disaster recovery (DR) planning.

Caveats to using checkpointing include: Whenever Worker Node state is destroyed (that is, when a Worker ceases to exist), checkpoint information will be lost. In Cribl.Cloud deployments, this can happen during upgrades. Also, because checkpoints are not written to disk on **every** event, the checkpoints written to disk will lag behind those that the S3 Source manages in memory. If the Worker Process stops abruptly without a clean shutdown, checkpoints not yet written to disk will be lost. When processing resumes, the Source will start from just after the last checkpoint written to disk, producing duplicate events corresponding to the lost checkpoints.

Located in the UI at **Advanced Settings** > **Checkpoint Settings** > **Enable checkpointing**, this feature is turned off by default.

### Connected Destinations {#connected}

Select **Send to Routes** to enable conditional routing, filtering, and cloning of this Source's data via the Routing table.

Select **QuickConnect** to send this Source's data to one or more Destinations via independent, direct connections.

## Internal Fields

Cribl Stream uses a set of internal fields to assist in handling of data. These "meta" fields are **not** part of an event, but they are accessible, and [Functions](functions) can use them to make processing decisions.

Fields for this Source:

- `__final`
- `__inputId`
- `__source`
- `_time`

## How to Configure S3 to Send Event Notifications to SQS {#configure_s3}

1. Create a Standard SQS Queue. Note its ARN.

2. Replace its access policy with one similar to the examples below. To do so, select the queue; and then, in the **Permissions** tab, click: **Edit Policy Document (Advanced)**. (These examples differ only at line 9, showing public access to the SQS queue versus S3-only access to the queue.)

3. In the Amazon S3 console, add a notification configuration to publish events of the `s3:ObjectCreated:*` type to the SQS queue.

{{% tabs "permissive" "permissive" "Permissive SQS access policy" "restrictive" "Restrictive SQS access policy" %}}
{{% tab-item "permissive" %}}

```json
{
  "Version": "2012-10-17",
  "Id": "example-ID",
  "Statement": [
    {
      "Sid": "<SID name>",
      "Effect": "Allow",
      "Principal": {
        "AWS": "*"
      },
      "Action": ["SQS:SendMessage"],
      "Resource": "example-SQS-queue-ARN",
      "Condition": {
        "ArnLike": { "aws:SourceArn": "arn:aws:s3:*:*:example-bucket-name" }
      }
    }
  ]
}
```

{{% /tab-item %}}
{{% tab-item "restrictive" %}}

```json
{
  "Version": "2012-10-17",
  "Id": "example-ID",
  "Statement": [
    {
      "Sid": "<SID name>",
      "Effect": "Allow",
      "Principal": {
        "Service": "s3.amazonaws.com"
      },
      "Action": ["SQS:SendMessage"],
      "Resource": "example-SQS-queue-ARN",
      "Condition": {
        "ArnLike": { "aws:SourceArn": "arn:aws:s3:*:*:example-bucket-name" }
      }
    }
  ]
}
```

{{% /tab-item %}}
{{% /tabs %}}

## S3 and SQS Permissions

The following permissions are required on the S3 bucket:

- `s3:GetObject`
- `s3:ListBucket`

Needed when **Tag after processing** is toggled on:

- `s3:GetObjectTagging`
- `s3:PutObjectTagging`

The following permissions are required on the SQS queue:

- `sqs:ReceiveMessage`
- `sqs:DeleteMessage`
- `sqs:ChangeMessageVisibility`
- `sqs:GetQueueAttributes`
- `sqs:GetQueueUrl`

To collect encrypted data, such as CloudTrail logs, you'll need to add access to the KMS resources in your KMS Key Policy. Add the IAM user or role to the **Principal** section of your Key Policy and provide the `kms:Decrypt` **Action**. See Amazon's documentation on [Key Policies](https://docs.aws.amazon.com/kms/latest/developerguide/key-policies.html) for more information, including how to [view](https://docs.aws.amazon.com/kms/latest/developerguide/key-policy-viewing.html) and [change](https://docs.aws.amazon.com/kms/latest/developerguide/key-policy-modifying.html) your policy.

### Temporary Access via SSO Provider

You can use [Okta](/iam/usecase-sso-okta#temp-access) or [SAML](/iam/usecase-sso-saml#temp-access) to provide access to S3 buckets using temporary security credentials.

## Proxying Requests

If you need to proxy HTTP/S requests, see [System Proxy Configuration](proxy-config).

## How Cribl Stream Pulls Data

Workers poll message from SQS. The call will return messages if they are available, or will time out after 1 second if no messages are available.

Each Worker gets its share of the load from S3. By default, S3 returns a maximum of 1 message in a single poll request. You can change this default in **Max messages**.

## Best Practices

Beyond these basics, also see our [Amazon S3 Better Practices](/stream/usecase-s3-better-practices) and [Using S3 Storage and Replay](/stream/usecase-replay-s3) guides:

- When Cribl Stream instances are deployed on AWS, use IAM Roles whenever possible.
  - Not only is this safer, but it also makes the configuration simpler to maintain.

- Although optional, we highly recommend that you use a **Filename Filter**.
  - This will ensure that Cribl Stream ingests only files of interest.
  - Ingesting only what's strictly needed improves latency, processing power, and data quality.

- If higher throughput is needed, increase **Advanced Settings** > **Number of Receivers** and/or **Max messages**. However, do note:
  - These are set at `1` by default. As a result, **each** Worker Process, in **each** Cribl Stream Worker Node, will run one receiver consuming one message (that is, S3 file) at a time.
  - Total S3 objects processed at a time per Worker Node = Worker Processes x Number of Receivers x Max Messages
  - Increased throughput implies additional CPU utilization.

- When ingesting large files, tune up the **Visibility Timeout**, or consider using smaller objects.
  - The default value of `600s` works well in most cases, and while you certainly can increase it, we suggest that you also consider using smaller S3 objects.

## Troubleshooting

The Source's configuration modal has helpful tabs for troubleshooting:

**Live Data**: Try capturing [live data](sources#capture-source-data) to see real-time events as they are ingested. On the **Live Data** tab, click **Start Capture** to begin viewing real-time data.

**Logs**: Review and search the logs that provide detailed information about the ingestion process, including any errors or warnings that may have occurred.

You can also view the [Monitoring](monitoring) page that provides a comprehensive overview of data volume and rate, helping you identify ingestion issues. Analyze the graphs showing events and bytes in/out over time.

> [Cribl University](https://university.cribl.io) offers an Advanced Troubleshooting > [Source Integrations: S3](https://university.cribl.io/amazon-s3-source) short course. To follow the direct course link, first log into your Cribl University account. (To create an account, select the **Sign up** link. You'll need to click through a short **Terms & Conditions** presentation, with chill music, before proceeding to courses - but Cribl's training is always free of charge.) Once logged in, check out other useful [Advanced Troubleshooting](https://university.cribl.io/page/all-courses-catalog) short courses and [Troubleshooting Criblets](https://university.cribl.io/page/all-courses-catalog).

{.box .course}

### VPC Endpoints

VPC [endpoints](https://docs.aws.amazon.com/vpc/latest/userguide/vpc-endpoints.html) for [SQS](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-vpc-endpoints.html) and for [S3](https://docs.aws.amazon.com/vpc/latest/userguide/vpc-endpoints-s3.html) might need to be set up in your account. Check with your administrator for details.

### Connectivity Issues

If you're having connectivity issues, but having no problems with the CLI, see if the [AWS CLI proxy](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-proxy.html) is in use. Check with your administrator for details.

### Common Issues

### "InvalidClientTokenId" or "The security token included in the request is invalid"

Can occur when using `AssumeRole` to access resources in a non-default AWS region (for example, `eu-central-2` / Zurich). This happens because:

- Instance profile credentials are typically minted by the global STS endpoint.
- Non-default regions (those disabled by default on AWS accounts) do not accept STS tokens minted by the global endpoint.

##### Recommendation

Set the `CRIBL_AWS_STS_REGION` environment variable to a default region that will accept your source credentials (for example, `eu-central-1` or `eu-west-1`), or set `CRIBL_AWS_STS_ENDPOINT` explicitly (for example, `https://sts.eu-west-1.amazonaws.com`).

Tokens minted by a regional STS endpoint are valid in any region, including non-default regions. For more information, see [Troubleshooting STS in Non-Default AWS Regions](/stream/usecase-aws-x-account#troubleshooting-sts-in-non-default-aws-regions).

### "Inaccessible host: 'sqs.us-east-1.amazonaws.com'. This service may not be available..."

**Full Error Text:** `"message":"Inaccessible host: 'sqs.us-east-1.amazonaws.com'. This service may not be available in the 'us-east-1' region.","stack":"UnknownEndpoint: Inaccessible host: 'sqs.us-east-1.amazonaws.com'. This service may not be available in the 'us-east-1' region.`

If this is persistent rather than intermittent then it could be caused by TLS negotiation failures. For example, AWS SQS currently does not support TLS 1.3. If intermittent then a network-related issue could be occurring such as DNS-related problems.

### "Missing credentials in config" or "stack:Error: connect ETIMEDOUT 169.254.169.254:80"

Can occur when **Authentication** is set to **Auto**, but no IAM role is attached.

##### Recommendation

Change to Manual Authentication; attach an IAM role; or if using IMDv2, switch to IMDv1 (if possible).

![Missing credentials in config](Screen_Shot_2021-04-12_at_11.22.57_PM.6869e9ac15.png)
{scale="40%" border="true"}

### "Bucket does not exist - self signed certificate"

**Example Error Text:**

```
message: Bucket does not exist - self signed certificate
stack: Error: self signed certificate
    at TLSSocket.onConnectSecure (node:_tls_wrap_1535:34)
    at TLSSocket.emit (node:events:513:28)
    at TLSSocket.emit (node:events:489:12)
    at TLSSocket._fini... Show more
tmpPath: /opt/cribl/s3_bucket/cribl/2023/03/28/CriblOut-XTNGh5.0.json.tmp
```

Can occur when:

- the Destination uses a self-signed cert that has not yet been trusted by Cribl Stream.
- the user does not have appropriate permissions to view the bucket.
- the authentication token generated by your computer has a timestamp that is out of sync with the server's time, resulting in an authentication failure.

##### Recommendation

Disable **Advanced Settings** > **Reject unauthorized certificates** or; get the sender's cert and add it to the `NODE_EXTRA_CA_CERTS` path for validation; or verify that the user has appropriate permissions.
