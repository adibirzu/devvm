#!/bin/bash
# OCI Remote Development Server - Status Script
# ==============================================
# Shows current status of the remote development server

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Parse value from OCI config file for a given profile and key.
get_oci_config_value() {
    local profile="$1"
    local key="$2"
    awk -v profile="[$profile]" -v key="$key" '
        $0 == profile { found=1; next }
        found && /^\[/ { found=0 }
        found && $0 ~ "^"key"[[:space:]]*=" {
            sub(/^[^=]*=[[:space:]]*/, "");
            print;
            exit
        }
    ' ~/.oci/config
}

run_with_timeout() {
    local seconds="$1"
    shift
    if command -v timeout &>/dev/null; then
        timeout "$seconds" "$@"
    elif command -v gtimeout &>/dev/null; then
        gtimeout "$seconds" "$@"
    else
        perl -e 'alarm shift; exec @ARGV' "$seconds" "$@"
    fi
}

# Load config
[[ -f "$PROJECT_DIR/.env.local" ]] && { set -a; source "$PROJECT_DIR/.env.local"; set +a; }

PROVIDER="${CLOUD_PROVIDER:-OCI}"

echo -e "${CYAN}"
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║     Multi-Cloud Remote Development Server - Status               ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

LIFECYCLE_STATE="UNKNOWN"
PUBLIC_IP=""
SHAPE=""
OCPUS="N/A"
MEMORY="N/A"
TIME_CREATED="N/A"

if [[ "$PROVIDER" == "OCI" ]]; then
    # Detect OCI CLI
    if command -v oci &> /dev/null; then
        OCI_CLI="oci"
    elif [[ -x "$HOME/oci-cli/bin/oci" ]]; then
        OCI_CLI="$HOME/oci-cli/bin/oci"
    else
        echo -e "${RED}OCI CLI not found${NC}"
        exit 1
    fi

    # Get compartment OCID
    if [[ -z "${OCI_COMPARTMENT_OCID:-}" ]]; then
        OCI_TENANCY_OCID="${OCI_TENANCY_OCID:-$(get_oci_config_value "${OCI_PROFILE:-DEFAULT}" "tenancy")}"
        OCI_COMPARTMENT_OCID=$($OCI_CLI iam compartment list \
            --compartment-id "$OCI_TENANCY_OCID" \
            --compartment-id-in-subtree true \
            --all \
            --query "data[?name=='$OCI_COMPARTMENT_NAME'].id | [0]" \
            --raw-output \
            --profile "${OCI_PROFILE:-DEFAULT}" 2>/dev/null)
    fi

    # Get instance info
    INSTANCE_JSON=$($OCI_CLI compute instance list \
        --compartment-id "$OCI_COMPARTMENT_OCID" \
        --display-name "$VM_NAME" \
        --query "data[0]" \
        --profile "${OCI_PROFILE:-DEFAULT}" 2>/dev/null)

    if [[ -z "$INSTANCE_JSON" || "$INSTANCE_JSON" == "null" ]]; then
        echo -e "${RED}Instance '$VM_NAME' not found in OCI compartment $OCI_COMPARTMENT_NAME${NC}"
        exit 1
    fi

    INSTANCE_ID=$(echo "$INSTANCE_JSON" | jq -r '.id')
    LIFECYCLE_STATE=$(echo "$INSTANCE_JSON" | jq -r '.["lifecycle-state"]')
    SHAPE=$(echo "$INSTANCE_JSON" | jq -r '.shape')
    OCPUS=$(echo "$INSTANCE_JSON" | jq -r '.["shape-config"].ocpus')
    MEMORY=$(echo "$INSTANCE_JSON" | jq -r '.["shape-config"]["memory-in-gbs"]')
    TIME_CREATED=$(echo "$INSTANCE_JSON" | jq -r '.["time-created"]')

    # Get public IP
    VNIC_ID=$($OCI_CLI compute vnic-attachment list \
        --compartment-id "$OCI_COMPARTMENT_OCID" \
        --instance-id "$INSTANCE_ID" \
        --query "data[0].\"vnic-id\"" \
        --raw-output \
        --profile "${OCI_PROFILE:-DEFAULT}" 2>/dev/null)

    PUBLIC_IP=$($OCI_CLI network vnic get \
        --vnic-id "$VNIC_ID" \
        --query "data.\"public-ip\"" \
        --raw-output \
        --profile "${OCI_PROFILE:-DEFAULT}" 2>/dev/null)

elif [[ "$PROVIDER" == "AWS" ]]; then
    if ! command -v aws &>/dev/null; then
        echo -e "${RED}AWS CLI not found${NC}"
        exit 1
    fi
    INSTANCE_JSON=$(aws ec2 describe-instances \
        --filters "Name=tag:Name,Values=$VM_NAME" "Name=instance-state-name,Values=running,pending,stopping,stopped" \
        --region "${AWS_REGION:-us-east-1}" \
        --profile "${AWS_PROFILE:-default}" \
        --query "Reservations[0].Instances[0]" \
        --output json 2>/dev/null)

    if [[ -z "$INSTANCE_JSON" || "$INSTANCE_JSON" == "null" ]]; then
        echo -e "${RED}Instance '$VM_NAME' not found in AWS region ${AWS_REGION:-us-east-1}${NC}"
        exit 1
    fi

    INSTANCE_ID=$(echo "$INSTANCE_JSON" | jq -r '.InstanceId')
    LIFECYCLE_STATE=$(echo "$INSTANCE_JSON" | jq -r '.State.Name | upcase')
    SHAPE=$(echo "$INSTANCE_JSON" | jq -r '.InstanceType')
    TIME_CREATED=$(echo "$INSTANCE_JSON" | jq -r '.LaunchTime')
    PUBLIC_IP=$(echo "$INSTANCE_JSON" | jq -r '.PublicIpAddress')

elif [[ "$PROVIDER" == "GCP" ]]; then
    if ! command -v gcloud &>/dev/null; then
        echo -e "${RED}gcloud CLI not found${NC}"
        exit 1
    fi
    INSTANCE_JSON=$(gcloud compute instances describe "$VM_NAME" \
        --project "$GCP_PROJECT_ID" \
        --zone "${GCP_ZONE:-us-central1-a}" \
        --format="json(id, status, machineType, creationTimestamp, networkInterfaces[0].accessConfigs[0].natIP)" 2>/dev/null)

    if [[ -z "$INSTANCE_JSON" || "$INSTANCE_JSON" == "null" ]]; then
        echo -e "${RED}Instance '$VM_NAME' not found in GCP zone ${GCP_ZONE:-us-central1-a}${NC}"
        exit 1
    fi

    INSTANCE_ID=$(echo "$INSTANCE_JSON" | jq -r '.id')
    LIFECYCLE_STATE=$(echo "$INSTANCE_JSON" | jq -r '.status | upcase')
    SHAPE=$(echo "$INSTANCE_JSON" | jq -r '.machineType' | awk -F/ '{print $NF}')
    TIME_CREATED=$(echo "$INSTANCE_JSON" | jq -r '.creationTimestamp')
    PUBLIC_IP=$(echo "$INSTANCE_JSON" | jq -r '.networkInterfaces[0].accessConfigs[0].natIP')

elif [[ "$PROVIDER" == "AZURE" ]]; then
    if ! command -v az &>/dev/null; then
        echo -e "${RED}Azure CLI not found${NC}"
        exit 1
    fi
    INSTANCE_JSON=$(az vm show \
        --resource-group "${AZURE_RESOURCE_GROUP:-remote-dev-rg}" \
        --name "$VM_NAME" \
        --query "{id:id, vmSize:hardwareProfile.vmSize}" \
        --output json 2>/dev/null)

    if [[ -z "$INSTANCE_JSON" || "$INSTANCE_JSON" == "null" ]]; then
        echo -e "${RED}Instance '$VM_NAME' not found in Azure Resource Group ${AZURE_RESOURCE_GROUP:-remote-dev-rg}${NC}"
        exit 1
    fi

    POWER_STATE=$(az vm get-instance-view \
        --resource-group "${AZURE_RESOURCE_GROUP:-remote-dev-rg}" \
        --name "$VM_NAME" \
        --query "instanceView.statuses[?starts_with(code, 'PowerState')].code" \
        -o tsv 2>/dev/null)
    
    LIFECYCLE_STATE="UNKNOWN"
    if [[ "$POWER_STATE" == "PowerState/running" ]]; then
        LIFECYCLE_STATE="RUNNING"
    elif [[ "$POWER_STATE" == "PowerState/deallocated" || "$POWER_STATE" == "PowerState/stopped" ]]; then
        LIFECYCLE_STATE="STOPPED"
    fi

    INSTANCE_ID=$(echo "$INSTANCE_JSON" | jq -r '.id')
    SHAPE=$(echo "$INSTANCE_JSON" | jq -r '.vmSize')
    
    PUBLIC_IP=$(az vm list-ip-addresses \
        --resource-group "${AZURE_RESOURCE_GROUP:-remote-dev-rg}" \
        --name "$VM_NAME" \
        --query "[0].virtualMachine.network.publicIpAddresses[0].ipAddress" \
        -o tsv 2>/dev/null)
else
    echo -e "${RED}Unsupported cloud provider: $PROVIDER${NC}"
    exit 1
fi

# Status color
case $LIFECYCLE_STATE in
    RUNNING) STATE_COLOR="${GREEN}" ;;
    STOPPED|STOPPING) STATE_COLOR="${YELLOW}" ;;
    TERMINATED|TERMINATING) STATE_COLOR="${RED}" ;;
    *) STATE_COLOR="${CYAN}" ;;
esac

echo "Instance: $VM_NAME"
echo "State: ${STATE_COLOR}$LIFECYCLE_STATE${NC}"
echo "Shape: $SHAPE ($OCPUS OCPUs, ${MEMORY}GB RAM)"
echo "Created: $TIME_CREATED"
echo ""
echo "Public IP: ${CYAN}$PUBLIC_IP${NC}"
echo ""

if [[ "$LIFECYCLE_STATE" == "RUNNING" ]]; then
    # Get SSH key path
    SSH_PUB="${SSH_PUBLIC_KEY_PATH/#\~/$HOME}"
    SSH_KEY="${SSH_PUB%.pub}"
    SSH_CONTROL_PATH="/tmp/oci-remote-dev-status-%r@%h:%p"
    SSH_CHECK_OPTS=(-o ConnectTimeout=5 -o StrictHostKeyChecking=no -o BatchMode=yes -o ControlMaster=auto -o "ControlPath=$SSH_CONTROL_PATH" -o ControlPersist=120)

    echo -e "${GREEN}Connection Info:${NC}"
    if [[ -n "$SSH_KEY" && -f "$SSH_KEY" ]]; then
        echo "  SSH: ssh -i $SSH_KEY $ADMIN_USERNAME@$PUBLIC_IP"
        SSH_CHECK_OPTS+=(-i "$SSH_KEY" -o IdentitiesOnly=yes)
    else
        echo "  SSH: ssh $ADMIN_USERNAME@$PUBLIC_IP"
    fi
    echo "  WireGuard: Import configs/wireguard/client.conf"
    echo ""
    echo -e "${GREEN}After VPN connected:${NC}"
    echo "  RDP: $WG_SERVER_IP:$RDP_PORT"
    echo "  code-server: http://$WG_SERVER_IP:$CODE_SERVER_PORT"
    echo ""

    # Check SSH connectivity
    echo -n "SSH connectivity: "
    if run_with_timeout 3 bash -c "echo >/dev/tcp/$PUBLIC_IP/22" 2>/dev/null; then
        echo -e "${GREEN}Port open${NC}"
        # Try actual SSH
        if [[ -n "$SSH_KEY" && -f "$SSH_KEY" ]]; then
            if ssh "${SSH_CHECK_OPTS[@]}" "$ADMIN_USERNAME@$PUBLIC_IP" "echo" 2>/dev/null; then
                echo -e "  SSH auth: ${GREEN}OK${NC}"
            else
                echo -e "  SSH auth: ${YELLOW}Not ready (cloud-init may still be running)${NC}"
            fi
        fi
    else
        echo -e "${RED}Unreachable${NC}"
    fi

    # Check WireGuard port
    echo -n "WireGuard port: "
    if run_with_timeout 3 bash -c "echo >/dev/udp/$PUBLIC_IP/$WG_PORT" 2>/dev/null; then
        echo -e "${GREEN}OK${NC}"
    else
        echo -e "${YELLOW}Unable to verify (UDP)${NC}"
    fi
fi
