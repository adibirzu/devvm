#!/bin/bash
# OCI Remote Development Server - Interactive Setup Wizard
# =========================================================
# Creates .env configuration interactively

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_DIR/.env"

# Detect OCI CLI
detect_oci_cli() {
    if command -v oci &> /dev/null; then
        OCI_CLI="oci"
    elif [[ -x "$HOME/oci-cli/bin/oci" ]]; then
        OCI_CLI="$HOME/oci-cli/bin/oci"
    else
        echo -e "${RED}OCI CLI not found!${NC}"
        echo "Please install: https://docs.oracle.com/en-us/iaas/Content/API/SDKDocs/cliinstall.htm"
        exit 1
    fi
}

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

# Prompt with default value
prompt() {
    local var_name=$1
    local prompt_text=$2
    local default_value=$3
    local value

    if [[ -n "$default_value" ]]; then
        read -p "$prompt_text [$default_value]: " value
        value="${value:-$default_value}"
    else
        read -p "$prompt_text: " value
    fi

    eval "$var_name=\"$value\""
}

# Prompt yes/no
prompt_yn() {
    local var_name=$1
    local prompt_text=$2
    local default=$3
    local value

    while true; do
        read -p "$prompt_text (y/n) [$default]: " value
        value="${value:-$default}"
        case $value in
            [Yy]* ) eval "$var_name=true"; break;;
            [Nn]* ) eval "$var_name=false"; break;;
            * ) echo "Please answer y or n.";;
        esac
    done
}

# Show banner
show_banner() {
    clear
    echo -e "${BLUE}"
    echo "╔══════════════════════════════════════════════════════════════════╗"
    echo "║     OCI Remote Development Server - Setup Wizard                 ║"
    echo "║     Configure your remote development environment                ║"
    echo "╚══════════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

# List OCI profiles
list_oci_profiles() {
    echo -e "\n${CYAN}Available OCI Profiles:${NC}"
    grep "^\[" ~/.oci/config 2>/dev/null | tr -d '[]' | nl -w2 -s') '
    echo ""
}

# List compartments
list_compartments() {
    local profile=$1
    local tenancy_ocid=$2
    echo -e "\n${CYAN}Fetching compartments...${NC}"
    $OCI_CLI iam compartment list \
        --compartment-id "$tenancy_ocid" \
        --compartment-id-in-subtree true \
        --all \
        --query "data[?\"lifecycle-state\"=='ACTIVE'].[name,id]" \
        --output table \
        --profile "$profile" 2>/dev/null | head -30
}

# Detect SSH keys
detect_ssh_keys() {
    echo -e "\n${CYAN}Detected SSH public keys:${NC}"
    ls -1 ~/.ssh/*.pub 2>/dev/null | nl -w2 -s') ' || echo "No SSH keys found in ~/.ssh/"
    echo ""
}

# Main wizard
main() {
    show_banner

    echo -e "${GREEN}This wizard will help you configure your remote development server.${NC}"
    echo -e "${YELLOW}Press Enter to accept default values shown in [brackets].${NC}\n"

    # ========== Cloud Provider Selection ==========
    echo -e "\n${BLUE}=== Cloud Provider Selection ===${NC}"
    echo "  1) Oracle Cloud Infrastructure (OCI) [Recommended]"
    echo "  2) Amazon Web Services (AWS)"
    echo "  3) Google Cloud Platform (GCP)"
    echo "  4) Microsoft Azure"
    prompt CLOUD_PROVIDER "Select Cloud Provider" "1"

    case "$CLOUD_PROVIDER" in
        2) CLOUD_PROVIDER="AWS" ;;
        3) CLOUD_PROVIDER="GCP" ;;
        4) CLOUD_PROVIDER="Azure" ;;
        1|*) CLOUD_PROVIDER="OCI" ;;
    esac

    echo -e "Selected Cloud Target: ${GREEN}$CLOUD_PROVIDER${NC}"

    # Default variables to avoid empty values in write
    OCI_PROFILE=""
    OCI_TENANCY_OCID=""
    OCI_COMPARTMENT_NAME=""
    OCI_REGION=""
    AWS_PROFILE=""
    AWS_REGION=""
    AWS_INSTANCE_TYPE=""
    AWS_SUBNET_ID=""
    AWS_KEY_PAIR_NAME=""
    GCP_PROJECT_ID=""
    GCP_ZONE=""
    GCP_MACHINE_TYPE=""
    GCP_SUBNETWORK=""
    AZURE_SUBSCRIPTION_ID=""
    AZURE_RESOURCE_GROUP=""
    AZURE_LOCATION=""
    AZURE_VM_SIZE=""
    AZURE_SUBNET_ID=""
    MULTILLM_SOURCE_PATH=""

    VM_NAME="remote-dev-server"
    VM_SHAPE="VM.Standard.E6.Flex"
    VM_OCPUS=4
    VM_MEMORY_GB=32
    VM_BOOT_VOLUME_GB=100
    UBUNTU_VERSION="24.04"
    VCN_NAME="remote-dev-vcn"
    VCN_CIDR="10.0.0.0/16"
    SUBNET_CIDR="10.0.1.0/24"
    AVAILABILITY_DOMAIN=""
    EXISTING_VCN_OCID=""
    EXISTING_SUBNET_OCID=""

    if [[ "$CLOUD_PROVIDER" == "OCI" ]]; then
        detect_oci_cli
        list_oci_profiles
        prompt OCI_PROFILE "OCI CLI profile name" "DEFAULT"

        # Get tenancy info
        echo -e "\n${CYAN}Fetching tenancy information...${NC}"
        OCI_TENANCY_OCID=$(get_oci_config_value "$OCI_PROFILE" "tenancy")
        OCI_TENANCY_OCID=$($OCI_CLI iam region-subscription list \
            --tenancy-id "$OCI_TENANCY_OCID" \
            --query 'data[0]."tenancy-id"' \
            --raw-output \
            --profile "$OCI_PROFILE" 2>/dev/null) || \
        OCI_TENANCY_OCID=$(get_oci_config_value "$OCI_PROFILE" "tenancy")

        OCI_REGION=$(get_oci_config_value "$OCI_PROFILE" "region")

        echo -e "Tenancy OCID: ${GREEN}$OCI_TENANCY_OCID${NC}"
        echo -e "Region: ${GREEN}$OCI_REGION${NC}"

        list_compartments "$OCI_PROFILE" "$OCI_TENANCY_OCID"
        prompt OCI_COMPARTMENT_NAME "Compartment name (empty for tenancy root)" ""
        
        prompt VM_NAME "Instance name" "remote-dev-server"
        prompt VM_SHAPE "VM Shape" "VM.Standard.E6.Flex"
        prompt VM_OCPUS "Number of OCPUs" "4"
        prompt VM_MEMORY_GB "Memory in GB" "32"
        prompt VM_BOOT_VOLUME_GB "Boot volume size (GB)" "100"
        
        prompt VCN_NAME "VCN name" "remote-dev-vcn"
        prompt VCN_CIDR "VCN CIDR" "10.0.0.0/16"
        prompt SUBNET_CIDR "Subnet CIDR" "10.0.1.0/24"
        prompt AVAILABILITY_DOMAIN "Availability Domain (1-3, empty for auto)" ""

    elif [[ "$CLOUD_PROVIDER" == "AWS" ]]; then
        echo -e "\n${BLUE}=== AWS Configuration ===${NC}"
        prompt AWS_PROFILE "AWS CLI profile name" "default"
        prompt AWS_REGION "AWS Region" "us-east-1"
        prompt AWS_INSTANCE_TYPE "Instance Type" "t3.xlarge"
        prompt AWS_SUBNET_ID "Existing Subnet ID (leave empty for auto)" ""
        prompt AWS_KEY_PAIR_NAME "EC2 Key Pair Name" "remote-dev-key"
        
        prompt VM_NAME "Instance name" "remote-dev-server"
        VM_SHAPE="$AWS_INSTANCE_TYPE"
        VM_OCPUS="4"
        VM_MEMORY_GB="16"
        prompt VM_BOOT_VOLUME_GB "EBS Boot Volume size (GB)" "100"

    elif [[ "$CLOUD_PROVIDER" == "GCP" ]]; then
        echo -e "\n${BLUE}=== GCP Configuration ===${NC}"
        prompt GCP_PROJECT_ID "GCP Project ID" ""
        prompt GCP_ZONE "GCP Zone" "us-central1-a"
        prompt GCP_MACHINE_TYPE "GCP Machine Type" "e2-standard-4"
        prompt GCP_SUBNETWORK "GCP Subnetwork name" "default"
        
        prompt VM_NAME "Instance name" "remote-dev-server"
        VM_SHAPE="$GCP_MACHINE_TYPE"
        VM_OCPUS="4"
        VM_MEMORY_GB="16"
        prompt VM_BOOT_VOLUME_GB "Boot Disk size (GB)" "100"

    elif [[ "$CLOUD_PROVIDER" == "Azure" ]]; then
        echo -e "\n${BLUE}=== Azure Configuration ===${NC}"
        prompt AZURE_SUBSCRIPTION_ID "Azure Subscription ID" ""
        prompt AZURE_RESOURCE_GROUP "Azure Resource Group" "remote-dev-rg"
        prompt AZURE_LOCATION "Azure Location" "eastus"
        prompt AZURE_VM_SIZE "Azure VM Size" "Standard_D4s_v5"
        prompt AZURE_SUBNET_ID "Existing Subnet ID (leave empty for default)" ""
        
        prompt VM_NAME "Instance name" "remote-dev-server"
        VM_SHAPE="$AZURE_VM_SIZE"
        VM_OCPUS="4"
        VM_MEMORY_GB="16"
        prompt VM_BOOT_VOLUME_GB "OS Disk size (GB)" "100"
    fi

    # ========== SSH Configuration ==========
    echo -e "\n${BLUE}=== SSH Configuration ===${NC}"

    detect_ssh_keys
    prompt SSH_PUBLIC_KEY_PATH "SSH public key path" "~/.ssh/id_rsa.pub"

    # Verify SSH key exists
    local ssh_key_path="${SSH_PUBLIC_KEY_PATH/#\~/$HOME}"
    if [[ ! -f "$ssh_key_path" ]]; then
        echo -e "${YELLOW}SSH key not found at $ssh_key_path${NC}"
        prompt_yn CREATE_SSH_KEY "Create new SSH key?" "y"
        if [[ "$CREATE_SSH_KEY" == "true" ]]; then
            ssh-keygen -t rsa -b 4096 -f "${ssh_key_path%.pub}" -N ""
            echo -e "${GREEN}SSH key created!${NC}"
        fi
    fi

    # ========== WireGuard Configuration ==========
    echo -e "\n${BLUE}=== WireGuard VPN Configuration ===${NC}"

    prompt WG_PORT "WireGuard port" "51820"
    prompt WG_NETWORK "WireGuard network" "10.200.200.0/24"
    prompt WG_SERVER_IP "Server IP in VPN" "10.200.200.1"
    prompt WG_CLIENT_IP "Client IP (your device)" "10.200.200.2"
    echo -e "${CYAN}Routing mode: split tunnel (recommended, leaves your Mac DNS alone)"
    echo -e "or full tunnel (routes ALL traffic through the VM).${NC}"
    prompt WG_FULL_TUNNEL "Full tunnel? (true/false)" "false"
    prompt WG_DNS "DNS to push to clients (empty for split tunnel)" ""

    # ========== Desktop Configuration ==========
    echo -e "\n${BLUE}=== Desktop Configuration ===${NC}"

    echo -e "${CYAN}Desktop environments:${NC}"
    echo "  1) xfce - Lightweight, fast [Recommended]"
    echo "  2) gnome - Full-featured, heavier"
    echo "  3) kde - Feature-rich, customizable"
    prompt DESKTOP_ENV "Desktop environment" "xfce"

    prompt RDP_PORT "RDP port" "3389"
    prompt VNC_PORT "VNC port" "5901"

    # ========== Development Tools ==========
    echo -e "\n${BLUE}=== Development Tools ===${NC}"

    prompt_yn INSTALL_CLAUDE_CODE "Install Claude Code CLI?" "y"
    prompt_yn INSTALL_CODEX "Install Codex CLI (OpenAI)?" "y"
    prompt_yn INSTALL_GEMINI "Install Gemini CLI (Google)?" "y"
    prompt_yn INSTALL_CODE_SERVER "Install code-server (VS Code in browser)?" "y"

    if [[ "$INSTALL_CODE_SERVER" == "true" ]]; then
        prompt CODE_SERVER_PORT "code-server port" "8443"
    else
        CODE_SERVER_PORT="8443"
    fi

    # Additional agent CLIs — opt-in, default no.
    prompt_yn INSTALL_OPENCODE "Install OpenCode CLI?" "n"
    prompt_yn INSTALL_PI "Install pi coding agent? (needs Node >= 22)" "n"
    prompt_yn INSTALL_GROK "Install Grok CLI (xAI)?" "n"
    prompt_yn INSTALL_CLINE "Install Cline CLI?" "n"
    prompt_yn INSTALL_COPILOT_CLI "Install GitHub Copilot CLI? (needs Node >= 22)" "n"
    prompt_yn INSTALL_CURSOR_AGENT "Install Cursor agent CLI (terminal agent)?" "n"
    prompt_yn INSTALL_OLLAMA "Install Ollama local-LLM serving + client wiring?" "n"
    if [[ "$INSTALL_OLLAMA" == "true" ]]; then
        prompt OLLAMA_MODELS "Ollama models to pull after install (comma-separated)" ""
        prompt OLLAMA_DEFAULT_MODEL "Default local model for the claude-local alias" "qwen3-coder"
    else
        OLLAMA_MODELS=""
        OLLAMA_DEFAULT_MODEL="qwen3-coder"
    fi

    prompt_yn INSTALL_CURSOR "Install Cursor IDE?" "y"
    prompt_yn INSTALL_PODMAN "Install Podman local container tooling?" "y"
    prompt_yn INSTALL_GITHUB_CLI "Install GitHub CLI (gh)?" "y"
    prompt_yn INSTALL_CSP_CLIS "Install all CSP CLIs on the VM?" "y"
    if [[ "$INSTALL_CSP_CLIS" == "true" ]]; then
        INSTALL_OCI_CLI=true
        INSTALL_AWS_CLI=true
        INSTALL_GCP_CLI=true
        INSTALL_AZURE_CLI=true
    else
        prompt_yn INSTALL_OCI_CLI "Install OCI CLI?" "y"
        prompt_yn INSTALL_AWS_CLI "Install AWS CLI?" "y"
        prompt_yn INSTALL_GCP_CLI "Install Google Cloud CLI?" "y"
        prompt_yn INSTALL_AZURE_CLI "Install Azure CLI?" "y"
    fi

    prompt MULTILLM_SOURCE_PATH "Local MultiLLM source path (empty = clone the public repo)" ""

    prompt NODE_VERSION "Node.js version" "20"
    prompt PYTHON_VERSION "Python version" "3.12"

    # ========== Security Configuration ==========
    echo -e "\n${BLUE}=== Security Configuration ===${NC}"

    prompt ADMIN_USERNAME "Admin username" "devuser"
    prompt_yn AUTO_SECURITY_UPDATES "Enable automatic security updates?" "y"
    prompt_yn FIREWALL_STRICT "Strict firewall (services via VPN only)?" "y"

    # ========== Multi-Developer Configuration ==========
    echo -e "\n${BLUE}=== Multi-Developer Configuration ===${NC}"
    prompt_yn MULTI_DEV_ENABLED "Add additional developer users during initial VM creation?" "n"
    ADDITIONAL_DEV_ENV=""
    if [[ "$MULTI_DEV_ENABLED" == "true" ]]; then
        DEV_INDEX=2
        while true; do
            echo -e "\n${BLUE}=== Developer $DEV_INDEX Configuration ===${NC}"
            DEFAULT_DEV_NAME="dev$DEV_INDEX"
            DEFAULT_WG_IP="10.200.200.$((DEV_INDEX + 1))"
            DEFAULT_CODE_PORT=$((8443 + DEV_INDEX - 1))
            prompt DEV_NAME "Developer $DEV_INDEX Linux username" "$DEFAULT_DEV_NAME"
            prompt DEV_SSH_KEY_PATH "Developer $DEV_INDEX SSH public key path or raw key" ""
            prompt DEV_WG_IP "Developer $DEV_INDEX WireGuard VPN IP" "$DEFAULT_WG_IP"
            prompt DEV_CODE_SERVER_PORT "Developer $DEV_INDEX code-server port" "$DEFAULT_CODE_PORT"

            ADDITIONAL_DEV_ENV+=$'\n'"DEV_${DEV_INDEX}_NAME=\"$DEV_NAME\""
            ADDITIONAL_DEV_ENV+=$'\n'"DEV_${DEV_INDEX}_SSH_KEY_PATH=\"$DEV_SSH_KEY_PATH\""
            ADDITIONAL_DEV_ENV+=$'\n'"DEV_${DEV_INDEX}_WG_IP=\"$DEV_WG_IP\""
            ADDITIONAL_DEV_ENV+=$'\n'"DEV_${DEV_INDEX}_CODE_SERVER_PORT=$DEV_CODE_SERVER_PORT"

            prompt_yn ADD_ANOTHER_DEV "Add another developer?" "n"
            if [[ "$ADD_ANOTHER_DEV" != "true" ]]; then
                break
            fi
            DEV_INDEX=$((DEV_INDEX + 1))
        done
    fi

    # ========== API Keys (Optional) ==========
    echo -e "\n${BLUE}=== API Keys (Optional - can be set later) ===${NC}"
    echo -e "${YELLOW}Leave empty to configure later. Keys are stored in .env${NC}"

    prompt ANTHROPIC_API_KEY "Anthropic API Key (Claude)" ""
    prompt OPENAI_API_KEY "OpenAI API Key (Codex)" ""
    prompt GOOGLE_AI_API_KEY "Google AI API Key (Gemini)" ""
    prompt GITHUB_TOKEN "GitHub Personal Access Token" ""

    # ========== Generate .env ==========
    echo -e "\n${CYAN}Generating .env configuration...${NC}"

    cat > "$ENV_FILE" << EOF
# Remote Development VM Configuration
# Generated by setup wizard on $(date)
# ============================================================

CLOUD_PROVIDER="$CLOUD_PROVIDER"

# ================== OCI CONFIGURATION ==================
OCI_PROFILE="$OCI_PROFILE"
OCI_TENANCY_OCID="$OCI_TENANCY_OCID"
OCI_COMPARTMENT_NAME="$OCI_COMPARTMENT_NAME"
OCI_COMPARTMENT_OCID=""
OCI_REGION="$OCI_REGION"

# ================== AWS CONFIGURATION ==================
AWS_PROFILE="$AWS_PROFILE"
AWS_REGION="$AWS_REGION"
AWS_INSTANCE_TYPE="$AWS_INSTANCE_TYPE"
AWS_SUBNET_ID="$AWS_SUBNET_ID"
AWS_KEY_PAIR_NAME="$AWS_KEY_PAIR_NAME"

# ================== GCP CONFIGURATION ==================
GCP_PROJECT_ID="$GCP_PROJECT_ID"
GCP_ZONE="$GCP_ZONE"
GCP_MACHINE_TYPE="$GCP_MACHINE_TYPE"
GCP_SUBNETWORK="$GCP_SUBNETWORK"

# ================== AZURE CONFIGURATION ==================
AZURE_SUBSCRIPTION_ID="$AZURE_SUBSCRIPTION_ID"
AZURE_RESOURCE_GROUP="$AZURE_RESOURCE_GROUP"
AZURE_LOCATION="$AZURE_LOCATION"
AZURE_VM_SIZE="$AZURE_VM_SIZE"
AZURE_SUBNET_ID="$AZURE_SUBNET_ID"

# ================== COMMON VM CONFIGURATION ==================
VM_NAME="$VM_NAME"
VM_SHAPE="$VM_SHAPE"
VM_OCPUS=$VM_OCPUS
VM_MEMORY_GB=$VM_MEMORY_GB
VM_BOOT_VOLUME_GB=$VM_BOOT_VOLUME_GB
UBUNTU_VERSION="$UBUNTU_VERSION"
AVAILABILITY_DOMAIN="$AVAILABILITY_DOMAIN"

# ================== COMMON NETWORK CONFIGURATION ==================
EXISTING_VCN_OCID="$EXISTING_VCN_OCID"
EXISTING_SUBNET_OCID="$EXISTING_SUBNET_OCID"
VCN_NAME="$VCN_NAME"
VCN_CIDR="$VCN_CIDR"
SUBNET_CIDR="$SUBNET_CIDR"

# ================== SSH CONFIGURATION ==================
SSH_PUBLIC_KEY_PATH="$SSH_PUBLIC_KEY_PATH"

# ================== WIREGUARD VPN CONFIGURATION ==================
WG_PORT=$WG_PORT
WG_NETWORK="$WG_NETWORK"
WG_SERVER_IP="$WG_SERVER_IP"
WG_CLIENT_IP="$WG_CLIENT_IP"
WG_FULL_TUNNEL=$WG_FULL_TUNNEL
WG_DNS="$WG_DNS"

# ================== DESKTOP CONFIGURATION ==================
DESKTOP_ENV="$DESKTOP_ENV"
RDP_PORT=$RDP_PORT
VNC_PORT=$VNC_PORT

# ================== DEVELOPMENT TOOLS ==================
INSTALL_CLAUDE_CODE=$INSTALL_CLAUDE_CODE
INSTALL_CODEX=$INSTALL_CODEX
INSTALL_GEMINI=$INSTALL_GEMINI
INSTALL_OPENCODE=${INSTALL_OPENCODE:-false}
INSTALL_PI=${INSTALL_PI:-false}
INSTALL_GROK=${INSTALL_GROK:-false}
INSTALL_CLINE=${INSTALL_CLINE:-false}
INSTALL_COPILOT_CLI=${INSTALL_COPILOT_CLI:-false}
INSTALL_CURSOR_AGENT=${INSTALL_CURSOR_AGENT:-false}
INSTALL_OLLAMA=${INSTALL_OLLAMA:-false}
OLLAMA_BIND_ADDRESS="${OLLAMA_BIND_ADDRESS:-}"
OLLAMA_PORT=${OLLAMA_PORT:-11434}
OLLAMA_MODELS="$OLLAMA_MODELS"
OLLAMA_DEFAULT_MODEL="$OLLAMA_DEFAULT_MODEL"
INSTALL_CODE_SERVER=$INSTALL_CODE_SERVER
CODE_SERVER_PORT=$CODE_SERVER_PORT
INSTALL_CURSOR=$INSTALL_CURSOR
INSTALL_PODMAN=$INSTALL_PODMAN
INSTALL_GITHUB_CLI=$INSTALL_GITHUB_CLI
INSTALL_CSP_CLIS=$INSTALL_CSP_CLIS
INSTALL_OCI_CLI=$INSTALL_OCI_CLI
INSTALL_AWS_CLI=$INSTALL_AWS_CLI
INSTALL_GCP_CLI=$INSTALL_GCP_CLI
INSTALL_AZURE_CLI=$INSTALL_AZURE_CLI
INSTALL_MULTILLM_GATEWAY=${INSTALL_MULTILLM_GATEWAY:-true}
MULTILLM_GATEWAY_PORT=${MULTILLM_GATEWAY_PORT:-8080}
MULTILLM_SOURCE_PATH="$MULTILLM_SOURCE_PATH"
NODE_VERSION="$NODE_VERSION"
PYTHON_VERSION="$PYTHON_VERSION"

# ================== SECURITY CONFIGURATION ==================
ADMIN_USERNAME="$ADMIN_USERNAME"
AUTO_SECURITY_UPDATES=$AUTO_SECURITY_UPDATES
FIREWALL_STRICT=$FIREWALL_STRICT

# ================== API KEYS ==================
ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY"
OPENAI_API_KEY="$OPENAI_API_KEY"
GOOGLE_AI_API_KEY="$GOOGLE_AI_API_KEY"
GITHUB_TOKEN="$GITHUB_TOKEN"

# ================== MULTIPLE DEVELOPER CONFIGURATION ==================
MULTI_DEV_ENABLED=$MULTI_DEV_ENABLED
$ADDITIONAL_DEV_ENV
EOF

    chmod 600 "$ENV_FILE"

    echo ""
    echo -e "${GREEN}╔══════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║           Configuration saved to .env                            ║${NC}"
    echo -e "${GREEN}╠══════════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${GREEN}║${NC} Cloud Target: ${CYAN}$CLOUD_PROVIDER${NC}"
    echo -e "${GREEN}║${NC} Instance:     ${CYAN}$VM_NAME${NC}"
    echo -e "${GREEN}║${NC} Shape/Size:   ${CYAN}$VM_SHAPE${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    prompt_yn RUN_DEPLOY "Run deployment now?" "y"
    if [[ "$RUN_DEPLOY" == "true" ]]; then
        exec "$SCRIPT_DIR/deploy.sh"
    else
        echo -e "\n${YELLOW}To deploy later, run:${NC}"
        echo -e "  ${CYAN}./scripts/deploy.sh${NC}"
    fi
}

main "$@"
