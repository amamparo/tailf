"""The single CloudFormation stack for tailf.dev.

Topology
--------
::

    EventBridge hourly (cron 0 * * * ?) ─▶ Lambda (Docker, Python 3.14)
                                  │  reads/writes data.json in S3
                                  ▼
    S3 (private, OAC) ◀── CloudFront (HTTPS, apex domain) ◀── browser
        ├── index.html + prerendered SvelteKit assets   (BucketDeployment)
        └── data.json                                    (written by Lambda)

    Route 53 (existing zone, looked up) ── A + AAAA alias ─▶ CloudFront
    ACM cert (us-east-1, DNS-validated) ─────────────────────▶ CloudFront

Everything lives in us-east-1 so the ACM certificate is colocated with
CloudFront (its only valid region) and the Route 53 zone lookup resolves
against a concrete env. See ``app.py`` for the env rationale.
"""

import os

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    SecretValue,
    Size,
    Stack,
)
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as events_targets
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as route53_targets
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3_deployment
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

# --- Tunables / conventions -------------------------------------------------

# Name of the Secrets Manager secret holding the Anthropic API key. It is
# created/populated out of band (the key is never in source or CDK context);
# the Lambda is granted read access below.
ANTHROPIC_SECRET_NAME = "tailf/anthropic-api-key"

# Direct Anthropic API model id (NOT Bedrock). Mirrors the backend default.
ANTHROPIC_MODEL = "claude-haiku-4-5"

# Build contexts, resolved as ABSOLUTE paths anchored to this file so they hold
# regardless of the cwd cdk is invoked from (the root cdk.json runs `python
# infra/app.py` from the repo root, so bare "../backend" would miss).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Dockerfile build context for the pipeline image.
BACKEND_DIR = os.path.join(_REPO_ROOT, "backend")
# adapter-static output (run `just build` first).
FRONTEND_BUILD_DIR = os.path.join(_REPO_ROOT, "frontend", "build")


class TailfStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        domain_name: str,
        anthropic_api_key: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.domain_name = domain_name

        # ------------------------------------------------------------------
        # S3: single private bucket for the static site AND data.json.
        # Public access fully blocked; CloudFront reaches it via OAC.
        # Personal site → auto-delete objects + destroy on stack teardown.
        # ------------------------------------------------------------------
        site_bucket = s3.Bucket(
            self,
            "SiteBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # ------------------------------------------------------------------
        # DNS: look up the EXISTING hosted zone (never create one). This is a
        # context lookup resolved at synth and cached in cdk.context.json;
        # it requires the concrete account+region set on the stack env.
        # ------------------------------------------------------------------
        hosted_zone = route53.HostedZone.from_lookup(
            self,
            "Zone",
            domain_name=domain_name,
        )

        # ------------------------------------------------------------------
        # ACM: DNS-validated cert for the apex domain in THIS stack. Because
        # the stack runs in us-east-1, the cert is valid for CloudFront.
        # The cert covers exactly the name CloudFront serves (the apex) — we
        # do NOT add a www SAN, since the distribution and the Route 53 alias
        # records below are apex-only; a www SAN would be issued and
        # separately DNS-validated but never routed to the distribution.
        # ------------------------------------------------------------------
        certificate = acm.Certificate(
            self,
            "Certificate",
            domain_name=domain_name,
            validation=acm.CertificateValidation.from_dns(hosted_zone),
        )

        # ------------------------------------------------------------------
        # CloudFront: S3 origin fronted by OAC (modern replacement for OAI;
        # the origin construct wires the bucket policy automatically).
        # ------------------------------------------------------------------
        s3_origin = origins.S3BucketOrigin.with_origin_access_control(site_bucket)

        # data.json changes every ~30 min; keep its edge cache short so the
        # site reflects fresh pipeline output without a manual invalidation.
        # (BucketDeployment still invalidates on each frontend deploy.)
        data_json_cache_policy = cloudfront.CachePolicy(
            self,
            "DataJsonCachePolicy",
            comment="Short TTL for data.json (refreshed every ~30 min)",
            default_ttl=Duration.minutes(5),
            min_ttl=Duration.seconds(0),
            max_ttl=Duration.minutes(15),
            enable_accept_encoding_gzip=True,
            enable_accept_encoding_brotli=True,
        )

        # ------------------------------------------------------------------
        # Security + discovery response headers (one policy on every behavior):
        # HSTS, nosniff, X-Frame-Options/CSP frame-ancestors (clickjacking),
        # Referrer-Policy, a pragmatic CSP, Permissions-Policy, No-Vary-Search,
        # and a Link header advertising the sitemap / data feed / llms.txt.
        # The CSP keeps 'unsafe-inline' for script/style because SvelteKit emits
        # a small inline hydration bootstrap (and Svelte injects inline styles);
        # the high-value clickjacking defense is frame-ancestors 'none' (+ XFO).
        # ------------------------------------------------------------------
        response_headers = cloudfront.ResponseHeadersPolicy(
            self,
            "ResponseHeaders",
            comment="tailf security + discovery headers",
            security_headers_behavior=cloudfront.ResponseSecurityHeadersBehavior(
                strict_transport_security=cloudfront.ResponseHeadersStrictTransportSecurity(
                    access_control_max_age=Duration.days(730),
                    include_subdomains=True,
                    preload=True,
                    override=True,
                ),
                content_type_options=cloudfront.ResponseHeadersContentTypeOptions(override=True),
                frame_options=cloudfront.ResponseHeadersFrameOptions(
                    frame_option=cloudfront.HeadersFrameOption.DENY,
                    override=True,
                ),
                referrer_policy=cloudfront.ResponseHeadersReferrerPolicy(
                    referrer_policy=cloudfront.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN,
                    override=True,
                ),
                content_security_policy=cloudfront.ResponseHeadersContentSecurityPolicy(
                    content_security_policy=(
                        "default-src 'self'; "
                        "script-src 'self' 'unsafe-inline' https://gc.zgo.at; "
                        "connect-src 'self' https://tailf.goatcounter.com; "
                        "img-src 'self' data: https:; "
                        "style-src 'self' 'unsafe-inline'; "
                        "font-src 'self'; "
                        "manifest-src 'self'; "
                        "object-src 'none'; base-uri 'none'; "
                        "frame-ancestors 'none'; form-action 'none'"
                    ),
                    override=True,
                ),
            ),
            custom_headers_behavior=cloudfront.ResponseCustomHeadersBehavior(
                custom_headers=[
                    cloudfront.ResponseCustomHeader(
                        header="Permissions-Policy",
                        value=(
                            "accelerometer=(), autoplay=(), camera=(), display-capture=(), "
                            "encrypted-media=(), fullscreen=(), geolocation=(), gyroscope=(), "
                            "magnetometer=(), microphone=(), midi=(), payment=(), usb=()"
                        ),
                        override=True,
                    ),
                    # The page is byte-identical regardless of query string, so
                    # tracking/sort params shouldn't fragment caches.
                    cloudfront.ResponseCustomHeader(
                        header="No-Vary-Search", value="params", override=True
                    ),
                    # Advertise machine-readable resources (RFC 8288).
                    cloudfront.ResponseCustomHeader(
                        header="Link",
                        value=(
                            '</sitemap.xml>; rel="sitemap"; type="application/xml", '
                            '</data.json>; rel="alternate"; type="application/json", '
                            '</llms.txt>; rel="describedby"; type="text/plain"'
                        ),
                        override=True,
                    ),
                ]
            ),
        )

        # ------------------------------------------------------------------
        # Pretty-URL rewrite at the edge (viewer-request CloudFront Function):
        #   "/"          -> "/index.html"
        #   "/foo/"      -> "/foo/index.html"
        #   "/privacy"   -> "/privacy.html"   (extensionless -> prerendered .html)
        #   "/data.json" -> unchanged (already has an extension)
        # Truly missing keys then surface a real 404 (see error_responses) rather
        # than the old soft-404 that rewrote EVERYTHING to /index.html with 200.
        # Uses only ES3/5 string methods so it runs on either CF JS runtime.
        # ------------------------------------------------------------------
        rewrite_fn = cloudfront.Function(
            self,
            "PrettyUrlRewrite",
            runtime=cloudfront.FunctionRuntime.JS_2_0,
            code=cloudfront.FunctionCode.from_inline(
                "function handler(event) {\n"
                "  var request = event.request;\n"
                "  var uri = request.uri;\n"
                "  if (uri === '/') {\n"
                "    request.uri = '/index.html';\n"
                "    return request;\n"
                "  }\n"
                "  if (uri.charAt(uri.length - 1) === '/') {\n"
                "    request.uri = uri + 'index.html';\n"
                "    return request;\n"
                "  }\n"
                "  var seg = uri.substring(uri.lastIndexOf('/') + 1);\n"
                "  if (seg.indexOf('.') === -1) {\n"
                "    request.uri = uri + '.html';\n"
                "  }\n"
                "  return request;\n"
                "}\n"
            ),
        )

        distribution = cloudfront.Distribution(
            self,
            "Distribution",
            default_root_object="index.html",
            http_version=cloudfront.HttpVersion.HTTP2_AND_3,
            domain_names=[domain_name],
            certificate=certificate,
            minimum_protocol_version=cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
            default_behavior=cloudfront.BehaviorOptions(
                origin=s3_origin,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                response_headers_policy=response_headers,
                compress=True,
                function_associations=[
                    cloudfront.FunctionAssociation(
                        function=rewrite_fn,
                        event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
                    )
                ],
            ),
            additional_behaviors={
                # Same-origin data.json with its own short-TTL policy. The
                # path pattern is written WITHOUT a leading slash per
                # CloudFront's documented convention (CDK emits PathPattern
                # "data.json"; it still matches requests to /data.json).
                # No rewrite function here — data.json already has an extension.
                "data.json": cloudfront.BehaviorOptions(
                    origin=s3_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
                    cache_policy=data_json_cache_policy,
                    response_headers_policy=response_headers,
                    compress=True,
                ),
            },
            # Missing keys (S3+OAC returns 403 for these) and 404s get the real
            # custom 404 page WITH a real 404 status — no more soft-404. The
            # pretty-URL function above resolves genuine routes (e.g. /privacy)
            # before this fires.
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=404,
                    response_page_path="/404.html",
                    ttl=Duration.minutes(5),
                ),
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=404,
                    response_page_path="/404.html",
                    ttl=Duration.minutes(5),
                ),
            ],
        )

        # ------------------------------------------------------------------
        # Route 53: apex alias A + AAAA records → the CloudFront distribution.
        # ------------------------------------------------------------------
        cloudfront_target = route53.RecordTarget.from_alias(
            route53_targets.CloudFrontTarget(distribution)
        )
        route53.ARecord(
            self,
            "AliasA",
            zone=hosted_zone,
            record_name=domain_name,
            target=cloudfront_target,
        )
        route53.AaaaRecord(
            self,
            "AliasAAAA",
            zone=hosted_zone,
            record_name=domain_name,
            target=cloudfront_target,
        )

        # CAA: restrict cert issuance to AWS ACM (the CloudFront cert's CA).
        route53.CaaRecord(
            self,
            "Caa",
            zone=hosted_zone,
            record_name=domain_name,
            values=[
                route53.CaaRecordValue(flag=0, tag=route53.CaaTag.ISSUE, value="amazon.com"),
                route53.CaaRecordValue(flag=0, tag=route53.CaaTag.ISSUEWILD, value="amazon.com"),
            ],
        )

        # ------------------------------------------------------------------
        # Secrets: CREATE the Anthropic API key secret, seeded with the value
        # loaded from the local .env at synth time (see app.py).
        #
        # TRADEOFF (chosen for this solo project): SecretValue.unsafe_plain_text
        # writes the key as PLAINTEXT into the AWS::SecretsManager::Secret
        # resource in the synthesized template (cdk.out, gitignored) and the
        # CloudFormation console. The Lambda env var below pulls the value via a
        # dynamic reference, so the plaintext lives ONLY in this Secret resource,
        # not also in the function config.
        #
        # NOTE: this stack now OWNS the secret. If one with this name already
        # exists in the account (e.g. created out of band earlier), delete it
        # first or the CloudFormation create will fail.
        # ------------------------------------------------------------------
        anthropic_secret = secretsmanager.Secret(
            self,
            "AnthropicApiKey",
            secret_name=ANTHROPIC_SECRET_NAME,
            secret_string_value=SecretValue.unsafe_plain_text(anthropic_api_key),
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ------------------------------------------------------------------
        # Lambda: the pipeline as a Docker image (Python 3.14, AL2023). The
        # build context is backend/ (its Dockerfile builds the image). CDK
        # builds + pushes to an auto-managed ECR repo.
        # ------------------------------------------------------------------
        pipeline_fn = lambda_.DockerImageFunction(
            self,
            "PipelineFunction",
            # ARM64 so the image matches an Apple-Silicon build host (and it's
            # cheaper). The image platform is pinned to match — a mismatch here
            # makes the container fail to exec (Runtime.InvalidEntrypoint).
            architecture=lambda_.Architecture.ARM_64,
            code=lambda_.DockerImageCode.from_image_asset(
                BACKEND_DIR, platform=ecr_assets.Platform.LINUX_ARM64
            ),
            # Sized for the headless-Chromium render fallback: Chromium is
            # memory-heavy and writes its profile to /tmp, and rendering adds
            # wall-clock on top of the static pass. Drop these back to ~1024MB /
            # 120s / default /tmp if TAILF_RENDER_ENABLED is set to false.
            memory_size=2048,
            timeout=Duration.seconds(300),
            ephemeral_storage_size=Size.mebibytes(1024),
            environment={
                # S3FileSystem reads the bucket name from here in Lambda
                # (Config.from_env / the di module look it up under this name).
                "TAILF_BUCKET": site_bucket.bucket_name,
                # Direct Anthropic API model id (NOT Bedrock). Must match the
                # env var Config.from_env reads (TAILF_MODEL), otherwise the
                # backend silently falls back to its compiled-in default.
                "TAILF_MODEL": ANTHROPIC_MODEL,
                # The Anthropic SDK reads ANTHROPIC_API_KEY from the environment
                # (the backend builds the client as `Anthropic()` with no args —
                # see backend/tailf/di.py). We inject it as a CloudFormation
                # *dynamic reference*: `secret_value.unsafe_unwrap()` renders to
                # `{{resolve:secretsmanager:...}}`, so the function config holds a
                # reference, not a second plaintext copy — CloudFormation resolves
                # it at deploy. (The plaintext itself lives in the Secret resource
                # above, seeded from .env.) grant_read below additionally
                # authorizes the function role to read the secret.
                "ANTHROPIC_API_KEY": anthropic_secret.secret_value.unsafe_unwrap(),
                # The secret's name, exposed for a possible future code path that
                # fetches/rotates the key via the SDK at runtime. The active path
                # today is the ANTHROPIC_API_KEY env var above.
                "ANTHROPIC_SECRET_NAME": ANTHROPIC_SECRET_NAME,
            },
        )

        # Least-privilege grants:
        #  * the pipeline reads the current data.json and writes the merged one
        #  * the pipeline reads the Anthropic key secret
        site_bucket.grant_read_write(pipeline_fn)
        anthropic_secret.grant_read(pipeline_fn)

        # ------------------------------------------------------------------
        # Frontend deploy: push the prerendered SvelteKit build into the bucket
        # and invalidate CloudFront so the new assets go live. NOTE: `just build`
        # must run first to produce ../frontend/build.
        #
        # Two passes so each gets the right client-facing Cache-Control
        # (CloudFront forwards the S3 object's header to the viewer):
        #   * fingerprinted, content-addressed assets -> cache a year, immutable
        #   * everything else (HTML, robots, sitemap, icons, llms.txt, 404, …)
        #     -> always revalidate, so a redeploy is picked up immediately.
        # Both prune=False so neither wipes the Lambda-written data.json.
        # ------------------------------------------------------------------
        s3_deployment.BucketDeployment(
            self,
            "DeployImmutable",
            sources=[s3_deployment.Source.asset(FRONTEND_BUILD_DIR)],
            destination_bucket=site_bucket,
            exclude=["*"],
            include=["_app/immutable/*"],
            cache_control=[
                s3_deployment.CacheControl.from_string("public, max-age=31536000, immutable")
            ],
            prune=False,
        )
        s3_deployment.BucketDeployment(
            self,
            "DeploySite",
            sources=[s3_deployment.Source.asset(FRONTEND_BUILD_DIR)],
            destination_bucket=site_bucket,
            exclude=["_app/immutable/*"],
            cache_control=[
                s3_deployment.CacheControl.from_string("public, max-age=0, must-revalidate")
            ],
            distribution=distribution,
            distribution_paths=["/*"],
            # Don't wipe data.json (written by the Lambda, not in the build).
            prune=False,
        )

        # ------------------------------------------------------------------
        # Schedule: EventBridge fires the pipeline hourly, at the top of the
        # hour (cron minute 0 -> 00:00, 01:00, ... UTC).
        # ------------------------------------------------------------------
        events.Rule(
            self,
            "PipelineSchedule",
            schedule=events.Schedule.cron(minute="0"),
            targets=[events_targets.LambdaFunction(pipeline_fn)],
        )

        # ------------------------------------------------------------------
        # Outputs.
        # ------------------------------------------------------------------
        CfnOutput(self, "BucketName", value=site_bucket.bucket_name)
        CfnOutput(self, "DistributionDomainName", value=distribution.distribution_domain_name)
        CfnOutput(self, "SiteUrl", value=f"https://{domain_name}/")
