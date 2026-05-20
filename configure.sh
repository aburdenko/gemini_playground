#!/bin/bash
# Usage: source configure.sh
# This script delegates to the real configure script located in .scripts/

source "$(dirname "${BASH_SOURCE[0]}")/.scripts/configure.sh"
