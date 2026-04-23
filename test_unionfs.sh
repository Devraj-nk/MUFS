#!/usr/bin/env bash

set -u

FUSE_BINARY="${FUSE_BINARY:-./mini_unionfs}"
TEST_DIR="${TEST_DIR:-./unionfs_test_env}"
LOWER_DIR="$TEST_DIR/lower"
UPPER_DIR="$TEST_DIR/upper"
MOUNT_DIR="$TEST_DIR/mnt"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m'

fusermount_cmd() {
	if command -v fusermount3 >/dev/null 2>&1; then
		echo fusermount3
	elif command -v fusermount >/dev/null 2>&1; then
		echo fusermount
	else
		echo ""
	fi
}

is_mounted() {
	# Best-effort: check /proc/mounts (works on Linux/WSL)
	grep -qs " $MOUNT_DIR " /proc/mounts
}

do_unmount() {
	if is_mounted; then
		local fm
		fm="$(fusermount_cmd)"
		if [[ -n "$fm" ]]; then
			"$fm" -u "$MOUNT_DIR" >/dev/null 2>&1 || true
		fi
		umount "$MOUNT_DIR" >/dev/null 2>&1 || true
	fi
}

cleanup() {
	do_unmount
	rm -rf "$TEST_DIR" >/dev/null 2>&1 || true
}

trap cleanup EXIT

echo "Starting Mini-UnionFS Test Suite..."

if [[ ! -x "$FUSE_BINARY" ]]; then
	echo -e "${RED}ERROR${NC}: FUSE binary '$FUSE_BINARY' not found or not executable. Run 'make' first."
	exit 1
fi

# Setup (unmount old mount if present, then recreate test env)
do_unmount
rm -rf "$TEST_DIR" >/dev/null 2>&1 || true
mkdir -p "$LOWER_DIR" "$UPPER_DIR" "$MOUNT_DIR"
chmod -R u+rwX "$TEST_DIR" >/dev/null 2>&1 || true

echo "base_only_content" > "$LOWER_DIR/base.txt"
echo "to_be_deleted" > "$LOWER_DIR/delete_me.txt"

"$FUSE_BINARY" "$LOWER_DIR" "$UPPER_DIR" "$MOUNT_DIR" >/dev/null 2>&1 &

# Wait for mount to become usable
mounted_ok=0
for _ in {1..30}; do
	if [[ -f "$MOUNT_DIR/base.txt" ]]; then
		mounted_ok=1
		break
	fi
	sleep 0.1
done

if [[ "$mounted_ok" -ne 1 ]]; then
	echo -e "${RED}FAILED${NC}: mount did not become ready at '$MOUNT_DIR'."
	echo -e "${YELLOW}Hint${NC}: if you see 'Permission denied' from fusermount, check FUSE setup/group permissions and that '$MOUNT_DIR' is user-accessible."
	exit 1
fi

# Test 1: Visibility
echo -n "Test 1: Layer Visibility... "
if grep -q "base_only_content" "$MOUNT_DIR/base.txt" 2>/dev/null; then
	echo -e "${GREEN}PASSED${NC}"
else
	echo -e "${RED}FAILED${NC}"
fi

# Test 2: Copy-on-Write
echo -n "Test 2: Copy-on-Write... "
echo "modified_content" >> "$MOUNT_DIR/base.txt" 2>/dev/null || true

mount_hits=$(grep -c "modified_content" "$MOUNT_DIR/base.txt" 2>/dev/null || echo 0)
upper_hits=$(grep -c "modified_content" "$UPPER_DIR/base.txt" 2>/dev/null || echo 0)
lower_hits=$(grep -c "modified_content" "$LOWER_DIR/base.txt" 2>/dev/null || echo 0)

if [[ "$mount_hits" -eq 1 && "$upper_hits" -eq 1 && "$lower_hits" -eq 0 ]]; then
	echo -e "${GREEN}PASSED${NC}"
else
	echo -e "${RED}FAILED${NC}"
fi

# Test 3: Whiteout
echo -n "Test 3: Whiteout mechanism... "
rm -f "$MOUNT_DIR/delete_me.txt" 2>/dev/null || true

if [[ ! -f "$MOUNT_DIR/delete_me.txt" && -f "$LOWER_DIR/delete_me.txt" && -f "$UPPER_DIR/.wh.delete_me.txt" ]]; then
	echo -e "${GREEN}PASSED${NC}"
else
	echo -e "${RED}FAILED${NC}"
fi

echo "Test Suite Completed."
