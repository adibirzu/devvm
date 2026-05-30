#!/bin/bash
# OCI Remote Development Server - Destroy Script
# ===============================================
# Cleans up all resources created by the deployment

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

log() { echo -e "${GREEN}[$(date '+%H:%M:%S')]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

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

# Load config
load_config() {
    local env_file="$PROJECT_DIR/.env"
    [[ ! -f "$env_file" && -f "$PROJECT_DIR/.env.local" ]] && env_file="$PROJECT_DIR/.env.local"
    [[ ! -f "$env_file" ]] && error ".env not found"
    set -a
    source "$env_file"
    set +a
}

PROVIDER="${CLOUD_PROVIDER:-OCI}"

# Detect CLI and prerequisites based on cloud provider
detect_prerequisites() {
    if [[ "$PROVIDER" == "OCI" ]]; then
        if command -v oci &> /dev/null; then
            OCI_CLI="oci"
        elif [[ -x "$HOME/oci-cli/bin/oci" ]]; then
            OCI_CLI="$HOME/oci-cli/bin/oci"
        else
            error "OCI CLI not found"
        fi

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
    elif [[ "$PROVIDER" == "AWS" ]]; then
        if ! command -v aws &>/dev/null; then
            error "AWS CLI not found"
        fi
    elif [[ "$PROVIDER" == "GCP" ]]; then
        if ! command -v gcloud &>/dev/null; then
            error "gcloud CLI not found"
        fi
        if [[ -z "$GCP_PROJECT_ID" ]]; then
            error "GCP_PROJECT_ID is not configured in .env"
        fi
    elif [[ "$PROVIDER" == "AZURE" ]]; then
        if ! command -v az &>/dev/null; then
            error "Azure CLI not found"
        fi
    fi
}

destroy_oci() {
    echo -e "Instance: ${CYAN}$VM_NAME${NC}"
    echo -e "Compartment: ${CYAN}$OCI_COMPARTMENT_NAME${NC}"
    echo ""

    read -p "Type 'DESTROY' to confirm OCI teardown: " confirm
    [[ "$confirm" != "DESTROY" ]] && { echo "Aborted."; exit 0; }

    echo ""
    log "Finding instance..."

    INSTANCE_OCID=$($OCI_CLI compute instance list \
        --compartment-id "$OCI_COMPARTMENT_OCID" \
        --display-name "$VM_NAME" \
        --lifecycle-state RUNNING \
        --query "data[0].id" \
        --raw-output \
        --profile "${OCI_PROFILE:-DEFAULT}" 2>/dev/null)

    if [[ -n "$INSTANCE_OCID" && "$INSTANCE_OCID" != "null" ]]; then
        log "Terminating instance: $INSTANCE_OCID"
        $OCI_CLI compute instance terminate \
            --instance-id "$INSTANCE_OCID" \
            --preserve-boot-volume false \
            --force \
            --profile "${OCI_PROFILE:-DEFAULT}"
        log "Instance termination initiated"

        log "Waiting for instance to terminate..."
        sleep 30
    else
        warn "Instance not found or already terminated"
    fi

    # Ask about networking resources
    read -p "Also delete VCN and networking resources? (y/N): " del_net
    if [[ "$del_net" =~ ^[Yy]$ ]]; then
        log "Finding VCN..."
        VCN_OCID=$($OCI_CLI network vcn list \
            --compartment-id "$OCI_COMPARTMENT_OCID" \
            --display-name "$VCN_NAME" \
            --query "data[0].id" \
            --raw-output \
            --profile "${OCI_PROFILE:-DEFAULT}" 2>/dev/null)

        if [[ -n "$VCN_OCID" && "$VCN_OCID" != "null" ]]; then
            # Delete subnet first
            SUBNET_OCID=$($OCI_CLI network subnet list \
                --compartment-id "$OCI_COMPARTMENT_OCID" \
                --vcn-id "$VCN_OCID" \
                --query "data[0].id" \
                --raw-output \
                --profile "${OCI_PROFILE:-DEFAULT}" 2>/dev/null)

            if [[ -n "$SUBNET_OCID" && "$SUBNET_OCID" != "null" ]]; then
                log "Deleting subnet..."
                $OCI_CLI network subnet delete \
                    --subnet-id "$SUBNET_OCID" \
                    --force \
                    --profile "${OCI_PROFILE:-DEFAULT}" || true
                sleep 10
            fi

            # Delete security list
            SL_OCID=$($OCI_CLI network security-list list \
                --compartment-id "$OCI_COMPARTMENT_OCID" \
                --vcn-id "$VCN_OCID" \
                --query "data[?contains(\"display-name\", 'remote-dev')].id | [0]" \
                --raw-output \
                --profile "${OCI_PROFILE:-DEFAULT}" 2>/dev/null)

            if [[ -n "$SL_OCID" && "$SL_OCID" != "null" ]]; then
                log "Deleting security list..."
                $OCI_CLI network security-list delete \
                    --security-list-id "$SL_OCID" \
                    --force \
                    --profile "${OCI_PROFILE:-DEFAULT}" || true
            fi

            # Delete route table
            RT_OCID=$($OCI_CLI network route-table list \
                --compartment-id "$OCI_COMPARTMENT_OCID" \
                --vcn-id "$VCN_OCID" \
                --query "data[?contains(\"display-name\", 'remote-dev')].id | [0]" \
                --raw-output \
                --profile "${OCI_PROFILE:-DEFAULT}" 2>/dev/null)

            if [[ -n "$RT_OCID" && "$RT_OCID" != "null" ]]; then
                log "Deleting route table..."
                $OCI_CLI network route-table delete \
                    --rt-id "$RT_OCID" \
                    --force \
                    --profile "${OCI_PROFILE:-DEFAULT}" || true
            fi

            # Delete internet gateway
            IGW_OCID=$($OCI_CLI network internet-gateway list \
                --compartment-id "$OCI_COMPARTMENT_OCID" \
                --vcn-id "$VCN_OCID" \
                --query "data[0].id" \
                --raw-output \
                --profile "${OCI_PROFILE:-DEFAULT}" 2>/dev/null)

            if [[ -n "$IGW_OCID" && "$IGW_OCID" != "null" ]]; then
                log "Deleting internet gateway..."
                $OCI_CLI network internet-gateway delete \
                    --ig-id "$IGW_OCID" \
                    --force \
                    --profile "${OCI_PROFILE:-DEFAULT}" || true
                sleep 5
            fi

            # Delete VCN
            log "Deleting VCN..."
            $OCI_CLI network vcn delete \
                --vcn-id "$VCN_OCID" \
                --force \
                --profile "${OCI_PROFILE:-DEFAULT}" || true
        else
            warn "VCN not found"
        fi
    fi
}

destroy_aws() {
    echo -e "Instance Name: ${CYAN}$VM_NAME${NC}"
    echo -e "Region:        ${CYAN}${AWS_REGION:-us-east-1}${NC}"
    echo ""

    read -p "Type 'DESTROY' to confirm AWS teardown: " confirm
    [[ "$confirm" != "DESTROY" ]] && { echo "Aborted."; exit 0; }

    echo ""
    log "Finding AWS instance..."

    INSTANCE_ID=$(aws ec2 describe-instances \
        --filters "Name=tag:Name,Values=$VM_NAME" "Name=instance-state-name,Values=running,pending,stopping,stopped" \
        --region "${AWS_REGION:-us-east-1}" \
        --profile "${AWS_PROFILE:-default}" \
        --query "Reservations[0].Instances[0].InstanceId" \
        --output text 2>/dev/null)

    if [[ -n "$INSTANCE_ID" && "$INSTANCE_ID" != "None" ]]; then
        log "Terminating EC2 Instance: $INSTANCE_ID..."
        aws ec2 terminate-instances --instance-ids "$INSTANCE_ID" --region "${AWS_REGION:-us-east-1}" --profile "${AWS_PROFILE:-default}" >/dev/null
        log "Instance termination initiated"
        log "Waiting for termination..."
        sleep 20
    else
        warn "AWS instance not found or already terminated"
    fi

    read -p "Also delete associated Security Group? (y/N): " del_net
    if [[ "$del_net" =~ ^[Yy]$ ]]; then
        log "Deleting Security Group..."
        aws ec2 delete-security-group --group-name "remote-dev-security-group" --region "${AWS_REGION:-us-east-1}" --profile "${AWS_PROFILE:-default}" || true
        log "AWS Security Group removed"
    fi
}

destroy_gcp() {
    echo -e "Instance: ${CYAN}$VM_NAME${NC}"
    echo -e "Project:  ${CYAN}$GCP_PROJECT_ID${NC}"
    echo -e "Zone:     ${CYAN}${GCP_ZONE:-us-central1-a}${NC}"
    echo ""

    read -p "Type 'DESTROY' to confirm GCP teardown: " confirm
    [[ "$confirm" != "DESTROY" ]] && { echo "Aborted."; exit 0; }

    echo ""
    log "Terminating Google Cloud VM instance '$VM_NAME'..."
    gcloud compute instances delete "$VM_NAME" \
        --project "$GCP_PROJECT_ID" \
        --zone "${GCP_ZONE:-us-central1-a}" \
        --quiet || warn "GCP instance not found or already deleted"

    read -p "Also delete GCP Firewall rule? (y/N): " del_net
    if [[ "$del_net" =~ ^[Yy]$ ]]; then
        log "Deleting GCP Firewall rule 'allow-remote-dev'..."
        gcloud compute firewall-rules delete "allow-remote-dev" --project "$GCP_PROJECT_ID" --quiet || true
    fi
}

destroy_azure() {
    echo -e "Instance Name:  ${CYAN}$VM_NAME${NC}"
    echo -e "Resource Group: ${CYAN}${AZURE_RESOURCE_GROUP:-remote-dev-rg}${NC}"
    echo ""

    read -p "Type 'DESTROY' to confirm Azure teardown: " confirm
    [[ "$confirm" != "DESTROY" ]] && { echo "Aborted."; exit 0; }

    echo ""
    log "Terminating Azure VM instance '$VM_NAME'..."
    az vm delete \
        --resource-group "${AZURE_RESOURCE_GROUP:-remote-dev-rg}" \
        --name "$VM_NAME" \
        --yes || warn "Azure VM not found or already deleted"

    read -p "Also delete the entire Resource Group '${AZURE_RESOURCE_GROUP:-remote-dev-rg}'? (y/N): " del_net
    if [[ "$del_net" =~ ^[Yy]$ ]]; then
        log "Deleting Azure Resource Group..."
        az group delete --name "${AZURE_RESOURCE_GROUP:-remote-dev-rg}" --yes --no-wait || true
        log "Azure Resource Group deletion initiated"
    fi
}

main() {
    echo -e "${RED}"
    echo "╔══════════════════════════════════════════════════════════════════╗"
    echo "║     WARNING: This will DESTROY all remote dev resources!         ║"
    echo "╚══════════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"

    load_config
    detect_prerequisites

    echo -e "Cloud Provider: ${CYAN}$PROVIDER${NC}"
    echo ""

    if [[ "$PROVIDER" == "OCI" ]]; then
        destroy_oci
    elif [[ "$PROVIDER" == "AWS" ]]; then
        destroy_aws
    elif [[ "$PROVIDER" == "GCP" ]]; then
        destroy_gcp
    elif [[ "$PROVIDER" == "AZURE" ]]; then
        destroy_azure
    else
        error "Unsupported cloud provider: $PROVIDER"
    fi

    echo ""
    echo -e "${GREEN}Teardown process completed!${NC}"
}

main "$@"
