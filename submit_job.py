import boto3, datetime, json

BUCKET = "fluxquantumai-data"
REGION = "us-east-1"
SOURCE_URI = "s3://fluxquantumai-data/sagemaker/source/iceberg-v2-20260411-121134/source.tar.gz"

role = boto3.client("iam", region_name=REGION).get_role(RoleName="SageMakerExecutionRole")["Role"]["Arn"]
timestamp = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
job_name = f"iceberg-v2-{timestamp}"
hps = {"stage1_epochs":"50","stage2_epochs":"50","stage1_batch":"512","stage2_batch":"256","stage1_lr":"0.001","stage2_lr":"0.0003"}

sm = boto3.client("sagemaker", region_name=REGION)
sm.create_training_job(
    TrainingJobName=job_name,
    RoleArn=role,
    AlgorithmSpecification={
        "TrainingImage": f"763104351884.dkr.ecr.{REGION}.amazonaws.com/pytorch-training:2.1.0-cpu-py310-ubuntu20.04-sagemaker",
        "TrainingInputMode": "File",
    },
    HyperParameters=hps,
    InputDataConfig=[
        {"ChannelName":"features","DataSource":{"S3DataSource":{"S3DataType":"S3Prefix","S3Uri":f"s3://{BUCKET}/features/iceberg_v2","S3DataDistributionType":"FullyReplicated"}},"ContentType":"application/x-parquet","CompressionType":"None"},
        {"ChannelName":"labels",  "DataSource":{"S3DataSource":{"S3DataType":"S3Prefix","S3Uri":f"s3://{BUCKET}/labels/iceberg_v2",  "S3DataDistributionType":"FullyReplicated"}},"ContentType":"application/x-parquet","CompressionType":"None"},
    ],
    OutputDataConfig={"S3OutputPath": f"s3://{BUCKET}/sagemaker/models/{job_name}/"},
    ResourceConfig={"InstanceType":"ml.m5.4xlarge","InstanceCount":1,"VolumeSizeInGB":50},
    StoppingCondition={"MaxRuntimeInSeconds":86400},
    Environment={
        "SAGEMAKER_SUBMIT_DIRECTORY": SOURCE_URI,
        "SAGEMAKER_PROGRAM": "train_entry.py",
        "SM_HPS": json.dumps(hps),
    },
)
print(f"Job submitted: {job_name}")
