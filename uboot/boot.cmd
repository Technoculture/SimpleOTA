# U-Boot script for A/B partition updates
# Save this as /boot/boot.scr.txt and compile with mkimage

# Default boot variables
setenv active_rootfs rootfs.0
setenv fallback_rootfs rootfs.1
setenv boot_count 0
setenv max_boot_count 3
setenv upgrade_available 0
setenv target_rootfs ''
setenv target_version ''

# Load environment from storage
if load ${devtype} ${devnum}:1 ${loadaddr} /uEnv.txt; then
    env import -t ${loadaddr} ${filesize}
fi

# Check if upgrade is pending
if test "${upgrade_available}" = "1"; then
    # We're attempting to boot into the upgraded partition
    echo "Attempting to boot updated system..."
    setenv active_rootfs ${target_rootfs}
    
    # Increment boot count
    setexpr boot_count ${boot_count} + 1
    echo "Boot attempt ${boot_count} of ${max_boot_count}"
    
    # Check if we've exceeded max attempts
    if test ${boot_count} -ge ${max_boot_count}; then
        echo "Too many failed boot attempts, reverting to fallback partition"
        if test "${active_rootfs}" = "rootfs.0"; then
            setenv active_rootfs rootfs.1
        else
            setenv active_rootfs rootfs.0
        fi
        setenv boot_count 0
        setenv upgrade_available 0
        saveenv
    else
        saveenv
    fi
fi

# Boot from the active partition
echo "Booting from ${active_rootfs}"
if test "${active_rootfs}" = "rootfs.0"; then
    setenv rootpart 2  # mmcblk0p2
else
    setenv rootpart 3  # mmcblk0p3
fi

setenv bootargs console=ttyS0,115200 root=/dev/mmcblk0p${rootpart} rootwait

# Load kernel and device tree from boot partition
load ${devtype} ${devnum}:1 ${kernel_addr_r} /boot/zImage
load ${devtype} ${devnum}:1 ${fdt_addr_r} /boot/dtb/${fdtfile}

# Boot the system
bootz ${kernel_addr_r} - ${fdt_addr_r}

# If we get here, boot failed
echo "Boot failed, trying fallback partition"

# Switch to fallback partition
if test "${active_rootfs}" = "rootfs.0"; then
    setenv active_rootfs rootfs.1
    setenv rootpart 3
else
    setenv active_rootfs rootfs.0
    setenv rootpart 2
fi

setenv bootargs console=ttyS0,115200 root=/dev/mmcblk0p${rootpart} rootwait
saveenv

# Try booting from fallback
bootz ${kernel_addr_r} - ${fdt_addr_r}
