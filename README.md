![shrenk](/assets/shrenk-001.png)

# Raspberry Pi Disk Image Resizer

Reduce (Shrink) a raw disk image (.img) created with dd or other tools. 

This is a command-line helper that **shrinks** a Raspberry Pi (or similar) Linux disk image to the smallest practical size and **truncates** the file to reclaim the freed space. 


It also offers an option to visualise the current partition layout with a simple ASCII bar.

While it can work with any raw disk image that ends with an ext-family partition, **it was designed with the classic Raspberry Pi layout in mind**: a small FAT boot partition (`p1`) followed by a much larger root filesystem partition (`p2`).  The tool shrinks only that last rootfs partition, making the image easier to store, transfer, and flash to SD cards.

In the future this utility could be paired with `raspi-config` inside the image so that – after flashing and first boot – the Pi can automatically *expand* the filesystem to fill the full SD-card capacity. Now you can just manually expand the image to use the whole disk after flashing.

---

## Features

* Interactive menu – choose between:
  1. Display partition layout (non-destructive)
  2. Shrink & truncate image (destructive)
  3. Quit
* Automatically attaches the image to a free loop device and cleans up afterwards.
* Shrinks the *last* ext2/3/4 partition to **its minimum size plus a 100 MB safety margin**.
* Updates the partition table and truncates the image file so it contains no trailing, unused space.
* Prints an ASCII representation of the final partition map for quick visual inspection.
* Built-in sanity checks:
  * Verifies the last partition is ext2/3/4 before proceeding.
  * Skips shrinking if the partition is already at (or near) its minimal size.

## Why a safety margin?
`resize2fs -P` returns the absolute minimum block count required to hold the current data.  Such a filesystem would mount, but Linux often writes logs and temporary files during boot; without free blocks the system can misbehave.  The script therefore adds **100 MB** of head-room by default (adjustable inside the code via `SAFETY_MB`).

## Requirements

* Linux host with **root privileges** (the script calls `sudo` internally).
* Standard GNU/Linux utilities:
  `losetup`, `partprobe`, `udevadm`, `lsblk`, `blockdev`, `fdisk`, `parted`,
  `e2fsck`, `resize2fs`, `tune2fs`, `truncate`, `printf/echo`.
* Python 3.8+ (uses f-strings, type hints).

## Usage
```bash
# Always work on a copy!
cp original.img work.img

# Display help banner and interactive menu
sudo python3 shrenk.py work.img
```

### Example session (layout only)
```
$ sudo python3 shrenk.py work.img
... select option 1 ...
Disk image partition layout (each character ~1.7% of image):
|11111111111112222222222222222222222222|
Legend: 1: 256MB 2: 1500MB
```

### Example session (resize)
```
... select option 2 ...
Checking filesystem for errors...
Getting minimum filesystem size...
Target filesystem size (blocks) with 100 MB margin: 1246015
Resizing filesystem to target size (minimum + safety margin)...
...
Image truncated successfully.
Disk image partition layout (each character ~1.7% of image):
|11111111111112222222|
Legend: 1: 256MB 2: 650MB
```

## Limitations
* Only the **last** partition is resized; multi-partition images with data after the last ext partition are not supported.
* Works with **ext2/3/4** filesystems only.
* Uses `parted` scripting; very old `parted` versions (<2.3) may not recognise the scripted `resizepart` command.
* Tested on Fedora 39 and Debian 11 hosts.

## License
This project is released under the MIT License. See `LICENSE` for details.
