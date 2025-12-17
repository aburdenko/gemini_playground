#!/bin/bash

echo "BEGIN `date -u`"

# Authenticate to GCP
# Check if the GCP account is set

if ! gcloud auth list; then

echo """No GCP account is set. Please login using command

gcloud auth login"""

exit 1

fi

 

# Check if the GCP project is set

if ! gcloud config list project; then

echo """No GCP project is set. Please set project using command

gcloud config set project <project id>"""

exit 1

fi

 

# Get the GCP account and project

account=$(gcloud config list account --format "value(core.account)" | xargs)

project=$(gcloud config list project --format "value(core.project)" | xargs)

 

# Print the GCP account and project

echo "GCP account: $account"
echo "GCP project: $project"


# Set the reservation name

RESERVATION_NAME="reservation01-a3-highgpu-8g"
REGION="europe-west4"
OWNER_PROJECT_ID=$project
MACHINE_TYPE="a3-highgpu-8g" # This will reserve single A100 GPU Type

MINIMUM_CPU_PLATFORM="Intel Cascade Lake"
NUMBER_OF_VMS=1
NUMBER_OF_ACCELERATORS=8
ACCELERATOR_TYPE="nvidia-tesla-h100"

LOCAL_SSD_SIZE=375
LOCAL_SSD_INTERFACE="scsi"



# Create a loop to run the create reservation command every 30 seconds

flag=0
while true; do
 for ZONE in $zone;
do

    # Run the create reservation command

    gcloud compute reservations create $RESERVATION_NAME --machine-type=$MACHINE_TYPE --min-cpu-platform="$MINIMUM_CPU_PLAFORM" --vm-count=$NUMBER_OF_VMS --accelerator=count=$NUMBER_OF_ACCELERATORS,type="$ACCELERATOR_TYPE" --local-ssd=size=$LOCAL_SSD_SIZE,interface=$LOCAL_SSD_INTERFACE --zone=$ZONE --project=$OWNER_PROJECT_ID --require-specific-reservation

    # Check if the command succeeded

    if [[ $? -eq 0 ]]; then
        # counter=$((counter + 1))
        echo "COMMAND SUCCEEDED `date -u`"
        flag=1
        break
    fi

    echo "COMMAND FAILED `date -u`"

    # Sleep for 30 seconds

    echo "SLEEPING FOR 30 SECONDS `date -u`"
    sleep 30
done

 

if [[ $flag -eq 1 ]] ; then
    echo "COMMAND SUCCEEDED `date -u`"
    break
fi
done

# Handle exceptions gracefully
if [[ $? -ne 0 ]]; then

    echo "An error occurred while creating the reservation."
    echo "ERROR OCCURED `date -u`"
    exit 1
fi

 

# Success!
echo "The reservation was created successfully."
echo "RESERVATION CREATED SUCCESSFULLY `date -u`"