#!/usr/bin/env python3
"""
shrenk.py

A script to resize the filesystem and partition inside a Raspberry Pi (or similar) disk image 
and truncate the image file to remove unused trailing space, using Linux command-line tools.

This script automates the process described below:

1. Attach the disk image as a loop device with partitions scanned.
2. Run filesystem check and calculate the minimum filesystem size.
3. Resize the filesystem to the minimum size to reclaim empty space.
4. Resize the partition in the partition table to match the new filesystem size.
5. Detach and re-attach the loop device to refresh the mappings.
6. Calculate the new size for truncating the image file based on the partition offset and resized filesystem size.
7. Detach the loop device and truncate the image file to remove unused space.

Requirements:
- Linux environment with root privileges.
- Installed command-line utilities (all available in standard GNU/Linux distributions):
  losetup, partprobe, udevadm, lsblk, blockdev, fdisk, parted, e2fsck,
  resize2fs, tune2fs, truncate, printf/echo.
- The image must contain an ext2/3/4 filesystem in the last partition.
- Script uses subprocess to invoke these utilities.

Usage:
    sudo python3 shrenk.py /path/to/image.img

WARNING:
- Always work on a backup copy of your image file.
- This script performs destructive operations; improper use may corrupt your image.

"""

import subprocess
import sys
import re
import os
import time

def run_cmd(cmd, capture_output=True, check=True):
    """Run a shell command and return its output.
    Raises RuntimeError with detailed context if the command exits with a non-zero status (when *check* is True).
    """
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            check=check,
            text=True,
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.PIPE,  # always capture stderr for diagnostics
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Command failed: {cmd}\n"
            f"Exit code: {exc.returncode}\n"
            f"stdout: {exc.stdout}\n"
            f"stderr: {exc.stderr}"
        ) from exc

    return result.stdout.strip() if capture_output else None

def find_free_loop_device():
    """Find a free loop device."""
    out = run_cmd("losetup -f")
    return out

def attach_loop_device(image_path):
    """Attach image to a loop device with partitions scanned (-P). Returns the loop device name."""
    loop_dev = find_free_loop_device()
    run_cmd(f"sudo losetup -P {loop_dev} {image_path}", capture_output=False)
    # Ensure the kernel has created partition device nodes before proceeding
    # Trigger a rescan and wait for udev to settle so that /dev/loopXpY exists.
    run_cmd(f"sudo partprobe {loop_dev}", capture_output=False, check=False)
    run_cmd("sudo udevadm settle", capture_output=False, check=False)
    return loop_dev

def detach_loop_device(loop_dev):
    """Detach loop device."""
    run_cmd(f"sudo losetup -d {loop_dev}", capture_output=False)

def wait_for_device(dev_path: str, timeout: int = 10):
    """Block until *dev_path* exists or *timeout* seconds elapse."""
    start = time.time()
    while not os.path.exists(dev_path):
        if time.time() - start > timeout:
            raise RuntimeError(f"Device {dev_path} did not appear within {timeout} seconds")
        time.sleep(0.2)

def get_partition_start(loop_dev, part_num):
    """Get start sector of a partition."""
    output = run_cmd(f"sudo fdisk -l {loop_dev}")
    # Example line: /dev/loop0p2   *       2048  62521343 62519296 29.8G 83 Linux
    regex = re.compile(rf"{loop_dev}p{part_num}\s+\*?\s+(\d+)\s+(\d+)")
    match = regex.search(output)
    if not match:
        raise RuntimeError(f"Partition {part_num} not found on {loop_dev}")
    start_sector = int(match.group(1))
    end_sector = int(match.group(2))
    return start_sector, end_sector

def list_partition_numbers(loop_dev: str) -> list[int]:
    """Return integer partition numbers for *loop_dev* using lsblk."""
    output = run_cmd(f"lsblk -ln -o NAME,TYPE {loop_dev}")
    base = os.path.basename(loop_dev) + "p"
    part_nums: list[int] = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        name, typ = parts
        if typ != "part" or not name.startswith(base):
            continue
        suffix = name[len(base):]
        if suffix.isdigit():
            part_nums.append(int(suffix))
    return part_nums

def get_sector_size(loop_dev):
    """Get sector size (usually 512 bytes)."""
    output = run_cmd(f"blockdev --getss {loop_dev}")
    return int(output)

def display_image_layout(image_path: str, bar_width: int = 60):
    """Attach the image and print an ASCII bar showing partition positions."""
    loop_dev = attach_loop_device(image_path)
    try:
        sector_size = get_sector_size(loop_dev)
        fdisk_out = run_cmd(f"sudo fdisk -l {loop_dev}")
        part_regex = re.compile(rf"{re.escape(loop_dev)}p(\d+)\s+\*?\s+(\d+)\s+(\d+)")
        parts: list[tuple[int,int,int]] = []  # (num, start, end)
        for line in fdisk_out.splitlines():
            m = part_regex.search(line)
            if m:
                parts.append((int(m.group(1)), int(m.group(2)), int(m.group(3))))
        if not parts:
            print("[layout] No partitions found to display.")
            return
        total_sectors = max(end for _num, _start, end in parts) + 1
        bar = [' '] * bar_width
        for num, start, end in parts:
            left = int(start * bar_width / total_sectors)
            right = max(left + 1, int((end + 1) * bar_width / total_sectors))
            for i in range(left, min(right, bar_width)):
                bar[i] = str(num % 10)
        print("\nDisk image partition layout (each character ~{:.0f}% of image):".format(100 / bar_width))
        print('|' + ''.join(bar) + '|')
        legend = ' '.join(f"{num}: {((end-start+1)*sector_size)//(1024*1024)}MB" for num, start, end in parts)
        print("Legend:", legend)
    finally:
        detach_loop_device(loop_dev)

def e2fsck_partition(part_dev):
    """Run a filesystem check on ext filesystem without interactive prompts."""
    run_cmd(f"sudo e2fsck -f -y {part_dev}", capture_output=False)

def get_min_filesystem_blocks(part_dev):
    """Get minimum size in filesystem blocks for resize2fs."""
    output = run_cmd(f"sudo resize2fs -P {part_dev}")
    # sample output: resize2fs 1.45.5 (07-Jan-2020)
    # Estimated minimum size of the filesystem: 251648
    match = re.search(r"Estimated minimum size of the filesystem: (\d+)", output)
    if not match:
        raise RuntimeError(f"Failed to get minimum filesystem size from resize2fs output.")
    return int(match.group(1))

def get_block_size(part_dev):
    """Get filesystem block size from tune2fs."""
    output = run_cmd(f"sudo tune2fs -l {part_dev}")
    match = re.search(r"Block size:\s*(\d+)", output)
    if not match:
        raise RuntimeError(f"Failed to get block size for {part_dev}")
    return int(match.group(1))

def resize_filesystem(part_dev, blocks=None):
    """Resize filesystem. If blocks given, resize to that many blocks; else shrink to partition size."""
    if blocks:
        run_cmd(f"sudo resize2fs {part_dev} {blocks}", capture_output=False)
    else:
        run_cmd(f"sudo resize2fs {part_dev}", capture_output=False)

def resize_partition(loop_dev, part_num, new_end):
    """
    Resize partition (part_num) to new end using parted.
    new_end is string like '40GB', '30GiB', or percent '100%', or sector count '950000s'.
    Uses `--pretend-input-tty` and pipes the confirmation response so that shrinking
    operations do not cause parted to abort in non-interactive mode.
    """
    # Build an interactive command script for parted.
    # We send:
    #   resizepart <num> <end>
    #   Yes
    #   quit
    script = (
        f"resizepart {part_num} {new_end}\n"
        "Yes\n"
        "quit\n"
    )
    cmd = f"printf '{script}' | sudo parted {loop_dev} ---pretend-input-tty"
    run_cmd(cmd, capture_output=False)

def truncate_image(image_path, new_size):
    """Truncate image file to new_size bytes."""
    run_cmd(f"truncate -s {new_size} {image_path}", capture_output=False)

def assert_ext_filesystem(part_dev: str):
    """Abort if *part_dev* is not an ext2/3/4 filesystem."""
    fstype = run_cmd(f"sudo blkid -o value -s TYPE {part_dev}")
    if fstype not in {"ext2", "ext3", "ext4"}:
        raise RuntimeError(
            f"Unsupported filesystem type '{fstype}' on {part_dev}. "
            "This script only handles ext2/3/4."
        )

def can_shrink(loop_dev: str, part_num: int, target_bytes: int) -> bool:
    """Return True if the partition size (in bytes) exceeds *target_bytes*.

    A small 1-sector tolerance is allowed so that rounding differences do not
    prevent a shrink that would otherwise succeed.
    """
    start_sector, end_sector = get_partition_start(loop_dev, part_num)
    sector_size = get_sector_size(loop_dev)
    current_bytes = (end_sector - start_sector + 1) * sector_size
    # Allow a one-sector tolerance
    return current_bytes - target_bytes > sector_size

def main(image_path):
    print(f"Starting resize and truncate process for {image_path}")

    # Attach image
    print("Attaching image to loop device...")
    loop_dev = attach_loop_device(image_path)
    print(f"Image attached as {loop_dev}")

    try:
        # We assume last partition number is the highest numbered partition on loop device:
        part_nums = list_partition_numbers(loop_dev)
        if not part_nums:
            raise RuntimeError("No partitions found on the loop device.")
        last_part_num = max(part_nums)
        part_dev = f"{loop_dev}p{last_part_num}"
        wait_for_device(part_dev)
        # Early sanity check: ensure the partition hosts an ext-family filesystem
        assert_ext_filesystem(part_dev)
        print(f"Using last partition: {part_dev}")

        # Run filesystem check
        print("Checking filesystem for errors...")
        e2fsck_partition(part_dev)

        # Get minimal filesystem size in blocks
        print("Getting minimum filesystem size...")
        min_blocks = get_min_filesystem_blocks(part_dev)
        print(f"Minimum filesystem size (blocks): {min_blocks}")

        # Get block size
        block_size = get_block_size(part_dev)
        print(f"Filesystem block size: {block_size} bytes")

        # Apply safety margin so the filesystem is not 100% full after shrinking
        SAFETY_MB = 100  # extra free space to add (adjust to taste)
        extra_blocks = (SAFETY_MB * 1024 * 1024 + block_size - 1) // block_size
        target_blocks = min_blocks + extra_blocks
        print(f"Target filesystem size (blocks) with {SAFETY_MB} MB margin: {target_blocks}")

        # Check if shrinking is actually needed/possible
        fs_size_bytes_target = target_blocks * block_size
        if not can_shrink(loop_dev, last_part_num, fs_size_bytes_target):
            print("The partition is already at or near its target minimal size. No shrinking necessary.")
            return

        # Resize filesystem to target size
        print("Resizing filesystem to target size (minimum + safety margin)...")
        #input ("Press enter to continue...")
        resize_filesystem(part_dev, target_blocks)
        print("Filesystem resized.")

        # Get partition start sector and end sector
        start_sector, end_sector = get_partition_start(loop_dev, last_part_num)
        print(f"Partition {last_part_num} start sector: {start_sector}, current end sector: {end_sector}")

        # Calculate new partition size in bytes from target_blocks and block size
        sector_size = get_sector_size(loop_dev)
        fs_size_bytes = target_blocks * block_size
        print(f"Device sector size: {sector_size} bytes")

        # Calculate new end sector for partition = start_sector + filesystem size in sectors - 1
        fs_size_sectors = (fs_size_bytes + sector_size - 1) // sector_size  # ceil division
        new_end_sector = start_sector + fs_size_sectors - 1
        print(f"New partition end sector: {new_end_sector}")

        # Resize partition to new end (in sectors)
        print(f"Resizing partition {last_part_num} to end at sector {new_end_sector}...")
        resize_partition(loop_dev, last_part_num, f"{new_end_sector}s")
        print("Partition resized.")

    finally:
        # Detach loop device to flush changes and free mappings
        print(f"Detaching loop device {loop_dev} to refresh...")
        detach_loop_device(loop_dev)

    # Reattach loop device to refresh
    loop_dev = attach_loop_device(image_path)
    print(f"Reattached image as {loop_dev}")
    part_dev = f"{loop_dev}p{last_part_num}"
    wait_for_device(part_dev)

    # Calculate truncate size: partition start byte + filesystem size bytes
    # ext2/3/4 block sizes (1 KiB, 2 KiB, 4 KiB) are multiples of the usual 512-byte
    # device sector size, so `fs_size_bytes` is already sector-aligned and the
    # following computation is safe.  If you ever use unusual block/sector
    # combinations, a future-proof alternative is:
    #     truncate_size = (start_sector + fs_size_sectors) * sector_size
    start_sector, end_sector = get_partition_start(loop_dev, last_part_num)
    sector_size = get_sector_size(loop_dev)
    fs_size_bytes = target_blocks * block_size
    truncate_size = start_sector * sector_size + fs_size_bytes

    print(f"Calculated truncate size for image: {truncate_size} bytes (~{truncate_size//(1024*1024)} MB)")

    # Detach loop device before truncating
    print(f"Detaching loop device {loop_dev} before truncating image...")
    detach_loop_device(loop_dev)

    # Truncate the image file
    print("Truncating image file to remove unused trailing space...")
    truncate_image(image_path, truncate_size)
    print("Image truncated successfully.")

    # Show final partition map in ASCII.
    display_image_layout(image_path)

    print("Process complete. Verify your resized image before use.")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: sudo python3 shrenk.py /path/to/image.img")
        sys.exit(1)
    image_path = sys.argv[1]
  
    while True:
        choice = input(
            "Select an action for the image:\n"
            "  1) Display partition layout\n"
            "  2) Resize and truncate image\n"
            "  3) Quit\n"
            "Enter choice [1/2/3]: "
        ).strip()

        if choice == "1":
            display_image_layout(image_path)
            break
        elif choice == "2":
            main(image_path)
            break
        elif choice in {"3", "q", "Q"}:
            print("Exiting without changes.")
            sys.exit(0)
        else:
            print("Invalid selection. Please enter 1, 2, or 3.")

