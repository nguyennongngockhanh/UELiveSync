#!/bin/bash
# Unified test runner for LiveSync protocol tests.
# Compiles and runs all C++ test suites + Python tests.
#
# Usage:
#   ./run_all_tests.sh              # Run all tests
#   ./run_all_tests.sh --build-only # Just build, don't run
#   ./run_all_tests.sh --no-python  # Skip Python tests

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="${SCRIPT_DIR}/../.."
VECTORS_DIR="${PROJECT_ROOT}/Tests/Protocol/vectors/v1"
PYTHON_DIR="${PROJECT_ROOT}/Tests/Protocol"

BUILD_ONLY=false
SKIP_PYTHON=false

for arg in "$@"; do
    case "$arg" in
        --build-only) BUILD_ONLY=true ;;
        --no-python)  SKIP_PYTHON=true ;;
        --help|-h)
            echo "Usage: $0 [--build-only] [--no-python]"
            exit 0
            ;;
    esac
done

# ─── Colors ─────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BOLD='\033[1m'
NC='\033[0m'  # No Color

pass_count=0
fail_count=0

# ─── Helpers ────────────────────────────────────────────────────

run_suite() {
    local name="$1"
    local binary="$2"
    local args="$3"
    local pad_len=28

    # Pad name with dots
    local dots=""
    local name_len=${#name}
    if [ $name_len -lt $pad_len ]; then
        dots=$(printf '%*s' $((pad_len - name_len)) '' | tr ' ' '.')
    fi

    if ! [ -x "$binary" ]; then
        printf "${BOLD}%-28s${NC} ${RED}BUILD FAILED${NC}\n" "$name"
        fail_count=$((fail_count + 1))
        return 1
    fi

    local output
    if output=$($binary $args 2>&1); then
        # Extract count from output if possible
        local count=$(echo "$output" | grep -oP '\d+ (VECTORS|PRIMITIVES|TESTS|PASSED|STABLE)' | grep -oP '^\d+' | tail -1)
        if [ -z "$count" ]; then
            count=$(echo "$output" | grep -c "PASS" || true)
        fi
        printf "${BOLD}%-28s${NC} ${GREEN}PASS${NC} (%s)\n" "$name" "$count"
        pass_count=$((pass_count + 1))
    else
        printf "${BOLD}%-28s${NC} ${RED}FAIL${NC}\n" "$name"
        echo "$output" | sed 's/^/    /' | head -20
        fail_count=$((fail_count + 1))
    fi
}

# ─── Build C++ tests ───────────────────────────────────────────

echo ""
echo "${BOLD}=== Building C++ tests ===${NC}"
echo ""

cd "$SCRIPT_DIR"

CXX="${CXX:-g++}"
CXXFLAGS="-std=c++20 -O2 -I."

for src in test_primitives.cpp test_serializer.cpp test_deserializer.cpp test_roundtrip.cpp test_property.cpp test_cross_language.cpp test_fuzz.cpp; do
    bin="${src%.cpp}"
    printf "  Building %-30s" "$bin"
    if $CXX $CXXFLAGS -o "$bin" "$src" 2>/dev/null; then
        echo "${GREEN}OK${NC}"
    else
        echo "${RED}FAILED${NC}"
        fail_count=$((fail_count + 1))
    fi
done

# Bridge dispatch test (requires -DUELIVESYNC_BRIDGE_TESTING)
printf "  Building %-30s" "test_bridge_dispatch"
if $CXX $CXXFLAGS -DUELIVESYNC_BRIDGE_TESTING -o "test_bridge_dispatch" "test_bridge_dispatch.cpp" 2>/dev/null; then
    echo "${GREEN}OK${NC}"
else
    echo "${RED}FAILED${NC}"
    fail_count=$((fail_count + 1))
fi

if $BUILD_ONLY; then
    echo ""
    echo "Build-only mode. Exiting."
    exit $fail_count
fi

# ─── Run C++ tests ─────────────────────────────────────────────

echo ""
echo "${BOLD}=== Running C++ tests ===${NC}"
echo ""

run_suite "Primitives"       "./test_primitives"       ""
run_suite "Serializer"       "./test_serializer"       "$VECTORS_DIR"
run_suite "Deserializer"     "./test_deserializer"     "$VECTORS_DIR"
run_suite "Round-trip"       "./test_roundtrip"        "$VECTORS_DIR"
run_suite "Property"          "./test_property"         ""
run_suite "Cross-language"    "./test_cross_language"   "$VECTORS_DIR"
run_suite "Fuzz"              "./test_fuzz"             ""
run_suite "Bridge dispatch"   "./test_bridge_dispatch"  "$VECTORS_DIR"

# ─── Run Python tests ──────────────────────────────────────────

if ! $SKIP_PYTHON; then
    echo ""
    echo "${BOLD}=== Running Python tests ===${NC}"
    echo ""

    if command -v python3 &>/dev/null && [ -d "$PYTHON_DIR/tests" ]; then
        py_output=$(cd "$PYTHON_DIR" && python3 -m pytest tests/ -v --tb=short 2>&1)
        if echo "$py_output" | grep -q "passed"; then
            py_count=$(echo "$py_output" | grep -oP '\d+ passed' | grep -oP '^\d+')
            printf "${BOLD}%-28s${NC} ${GREEN}PASS${NC} (%s)\n" "Python parity" "$py_count"
            pass_count=$((pass_count + 1))
        else
            printf "${BOLD}%-28s${NC} ${RED}FAIL${NC}\n" "Python parity"
            echo "$py_output" | tail -10 | sed 's/^/    /'
            fail_count=$((fail_count + 1))
        fi

        # Cross-language verification (Python vs C++ output)
        if [ -f "$PYTHON_DIR/cross_language_verify.py" ] && [ -d "$VECTORS_DIR/cpp_serialized" ]; then
            xlang_output=$(cd "$PYTHON_DIR" && python3 cross_language_verify.py "$VECTORS_DIR" 2>&1)
            if echo "$xlang_output" | grep -q "ALL CROSS-LANGUAGE TESTS PASSED"; then
                xlang_count=$(python3 -c "import json;print(json.load(open('$VECTORS_DIR/manifest.json'))['vector_count'])" 2>/dev/null || echo "?")
                printf "${BOLD}%-28s${NC} ${GREEN}PASS${NC} (%s)\n" "Cross-language verify" "$xlang_count"
                pass_count=$((pass_count + 1))
            else
                printf "${BOLD}%-28s${NC} ${RED}FAIL${NC}\n" "Cross-language verify"
                echo "$xlang_output" | tail -15 | sed 's/^/    /'
                fail_count=$((fail_count + 1))
            fi
        fi
    else
        printf "${BOLD}%-28s${NC} ${YELLOW}SKIP${NC} (python3 or tests/ not found)\n" "Python parity"
    fi
fi

# ─── Summary ────────────────────────────────────────────────────

echo ""
echo "${BOLD}=== Summary ===${NC}"
echo ""

total=$((pass_count + fail_count))

if [ $fail_count -eq 0 ]; then
    echo -e "${GREEN}${BOLD}ALL $total SUITES PASSED${NC}"
    exit 0
else
    echo -e "${RED}${BOLD}$fail_count/$total SUITES FAILED${NC}"
    exit 1
fi
