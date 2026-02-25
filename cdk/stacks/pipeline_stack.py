from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    CfnOutput,
    aws_dynamodb as dynamodb,
    aws_lambda as lambda_,
    aws_events as events,
    aws_events_targets as targets,
    aws_apigateway as apigw,
    aws_s3 as s3,
    aws_iam as iam,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct
import os


class PipelineStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ─────────────────────────────────────────────
        # 1. DynamoDB Table
        #    Partition key: date (String, e.g. "2025-06-10")
        # ─────────────────────────────────────────────
        table = dynamodb.Table(
            self,
            "MoversTable",
            table_name="stocks-movers",
            partition_key=dynamodb.Attribute(
                name="date",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,  # free tier friendly
            removal_policy=RemovalPolicy.RETAIN,  # don't wipe data on cdk destroy
        )

        # ─────────────────────────────────────────────
        # 2. Reference the Massive API key secret
        #    (created manually via aws secretsmanager create-secret)
        # ─────────────────────────────────────────────
        api_key_secret = secretsmanager.Secret.from_secret_name_v2(
            self,
            "MassiveApiKeySecret",
            secret_name="stocks/massive-api-key",
        )

        # ─────────────────────────────────────────────
        # 3. Shared Lambda layer for the requests library
        # ─────────────────────────────────────────────
        requests_layer = lambda_.LayerVersion(
            self,
            "RequestsLayer",
            code=lambda_.Code.from_asset(
                os.path.join(os.path.dirname(__file__), "../../layers/requests")
            ),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
            description="requests library for Lambda",
        )

        # ─────────────────────────────────────────────
        # 4. Ingestion Lambda
        #    Triggered by EventBridge daily cron
        # ─────────────────────────────────────────────
        ingestion_fn = lambda_.Function(
            self,
            "IngestionFunction",
            function_name="stocks-ingestion",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.main",
            code=lambda_.Code.from_asset(
                os.path.join(os.path.dirname(__file__), "../../lambdas/ingestion")
            ),
            timeout=Duration.seconds(60),
            memory_size=128,
            layers=[requests_layer],
            environment={
                "TABLE_NAME": table.table_name,
                "SECRET_NAME": "stocks/massive-api-key",
            },
        )

        # Grant ingestion Lambda read/write on DynamoDB
        table.grant_write_data(ingestion_fn)

        # Grant ingestion Lambda permission to read the secret
        api_key_secret.grant_read(ingestion_fn)

        # ─────────────────────────────────────────────
        # 5. EventBridge Rule — fires daily at 9 PM UTC
        #    (5 PM ET, after US market close at 4 PM ET)
        # ─────────────────────────────────────────────
        rule = events.Rule(
            self,
            "DailyMarketCron",
            rule_name="stocks-daily-ingestion",
            description="Trigger stock ingestion Lambda daily after market close",
            schedule=events.Schedule.cron(
                minute="0",
                hour="21",   # 9 PM UTC = 5 PM ET
                month="*",
                week_day="MON-FRI",  # market days only
                year="*",
            ),
        )
        rule.add_target(targets.LambdaFunction(ingestion_fn))

        # ─────────────────────────────────────────────
        # 6. Query Lambda
        #    Separate function — serves GET /movers
        # ─────────────────────────────────────────────
        query_fn = lambda_.Function(
            self,
            "QueryFunction",
            function_name="stocks-query",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.main",
            code=lambda_.Code.from_asset(
                os.path.join(os.path.dirname(__file__), "../../lambdas/query")
            ),
            timeout=Duration.seconds(10),
            memory_size=128,
            environment={
                "TABLE_NAME": table.table_name,
            },
        )

        # Grant query Lambda read-only on DynamoDB
        table.grant_read_data(query_fn)

        # ─────────────────────────────────────────────
        # 7. API Gateway REST API
        # ─────────────────────────────────────────────
        api = apigw.RestApi(
            self,
            "StocksApi",
            rest_api_name="stocks-api",
            description="Stocks pipeline REST API",
            default_cors_preflight_options=apigw.CorsOptions(
                allow_origins=apigw.Cors.ALL_ORIGINS,
                allow_methods=["GET", "OPTIONS"],
                allow_headers=["Content-Type"],
            ),
        )

        movers_resource = api.root.add_resource("movers")
        movers_resource.add_method(
            "GET",
            apigw.LambdaIntegration(
                query_fn,
                proxy=True,
            ),
        )

        # ─────────────────────────────────────────────
        # 8. S3 Bucket — static website hosting
        # ─────────────────────────────────────────────
        website_bucket = s3.Bucket(
            self,
            "WebsiteBucket",
            bucket_name=f"stocks-pipeline-frontend-{self.account}",
            website_index_document="index.html",
            website_error_document="index.html",
            public_read_access=True,
            block_public_access=s3.BlockPublicAccess(
                block_public_acls=False,
                block_public_policy=False,
                ignore_public_acls=False,
                restrict_public_buckets=False,
            ),
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # ─────────────────────────────────────────────
        # 9. Outputs
        # ─────────────────────────────────────────────
        CfnOutput(
            self,
            "ApiUrl",
            value=api.url,
            description="API Gateway base URL — use this in the frontend config",
        )

        CfnOutput(
            self,
            "WebsiteUrl",
            value=website_bucket.bucket_website_url,
            description="S3 static website URL",
        )

        CfnOutput(
            self,
            "BucketName",
            value=website_bucket.bucket_name,
            description="S3 bucket name for frontend deployment",
        )

        CfnOutput(
            self,
            "TableName",
            value=table.table_name,
            description="DynamoDB table name",
        )
