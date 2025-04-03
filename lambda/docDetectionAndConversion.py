import json
import urllib.parse
import boto3
import io
import csv

print('Loading function')

s3 = boto3.client('s3')


def lambda_handler(event, context):
    # print("Received event: " + json.dumps(event, indent=2))

    # Get the object from the event and show its content type
    bucket = event['Records'][0]['s3']['bucket']['name']
    print("Bucket: " + bucket)

    key = urllib.parse.unquote_plus(event['Records'][0]['s3']['object']['key'], encoding='utf-8')
    print("Key: " + key)

    try:
        response = s3.get_object(Bucket=bucket, Key=key)

        for key in response:
            print(key + ": " + str(response[key]))
        print("CONTENT TYPE: " + response['ContentType'])


        # .csv file processing
        if (response['ContentType'] == 'text/csv'):
            print("Processing .csv file")
            data = response['Body'].read().decode('utf-8')
            reader = csv.reader(io.StringIO(data))
            # next(reader)  # skip header row

            for row in reader:
                print(row[0], row[1], row[2])


        # .txt file processing
        if (response['ContentType'] == 'text/plain'):
            print("Processing .txt file")
            data = response['Body'].read().decode('utf-8')
            print(data)




        return response['ContentType']
    except Exception as e:
        print(e)
        print('Error getting object {} from bucket {}. Make sure they exist and your bucket is in the same region as this function.'.format(key, bucket))
        raise e
