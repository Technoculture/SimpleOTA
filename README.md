# SimpleOTA

A lightweight Over-The-Air update system for embedded Linux devices with A/B partition support and Zenoh-based communication.

Copyright © 2025 Technoculture Research. Released under MIT License.

## Overview

SimpleOTA is a complete solution for managing software updates on embedded Linux systems. It provides:

- Reliable A/B partition updates with fallback capability
- Secure update bundles with cryptographic verification
- Efficient communication using the Zenoh protocol
- Robust rollout campaign management
- Simple integration with existing systems

## Features

- **A/B Partition Support**: Install updates to inactive partition for safe rollback
- **Secure Updates**: Cryptographically signed update bundles with integrity verification
- **Zenoh Communication**: Efficient pub/sub and queryable data for update distribution
- **Yocto Integration**: Complete meta-layer for easy integration
- **Rollout Management**: Staged rollouts to device groups with monitoring
- **Resource Efficient**: Small footprint suitable for constrained devices
- **U-Boot Integration**: Compatible with standard U-Boot bootloader

## Installation

### Using Yocto/OpenEmbedded

Add the meta-simpleota layer to your build:

```
git clone https://github.com/technoculture/meta-simpleota.git
bitbake-layers add-layer meta-simpleota
```

Add SimpleOTA to your image:

```
IMAGE_INSTALL:append = " simpleota"
```

### Manual Installation

For manual installation on a running system:

```bash
# Clone the repository
git clone https://github.com/technoculture/simpleota.git
cd simpleota

# Install dependencies
pip3 install zenoh cryptography

# Install the package
pip3 install .

# Create configuration directory
sudo mkdir -p /etc/simpleota
sudo cp config/default_config.json /etc/simpleota/config.json

# Set up systemd service
sudo cp systemd/simpleota.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable simpleota
sudo systemctl start simpleota
```

## Configuration

The main configuration file is located at `/etc/simpleota/config.json`:

```json
{
  "device_id": "auto-generated-if-empty",
  "groups": ["default"],
  "verification_key_path": "/etc/simpleota/update-server.pub",
  "topic_prefix": "simpleota",
  "check_interval": 3600,
  "auto_update": false,
  "auto_reboot": false,
  "reboot_delay": 60,
  "zenoh_connect_endpoints": null
}
```

## Security

SimpleOTA uses industry-standard cryptographic practices:

- RSA-2048 or higher for bundle signing
- SHA-256 for integrity verification
- Secure boot compatibility (when available)
- Protected bootloader environment

## Development

### Building Update Bundles

Use the provided bundle creation tool:

```bash
python3 -m simpleota.tools.bundle_create \
  --output my_update.bundle \
  --version 1.2.0 \
  --files rootfs=/path/to/rootfs.img,kernel=/path/to/Image \
  --key /path/to/private_key.pem
```

### Project Structure

```
simpleota/
├── src/
│   ├── device/          # Device-side update client
│   ├── server/          # Server-side update management
│   ├── common/          # Shared components
│   └── cli/             # Command-line interfaces
├── tools/               # Helper tools and scripts
├── examples/            # Example configurations
└── docs/                # Documentation
```

## License

SimpleOTA is released under the MIT License. See the LICENSE file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Credits

Developed and maintained by Technoculture Research.
