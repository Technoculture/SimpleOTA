#!/usr/bin/env python3
"""
EmbeddedUpdater - A RAUC-like software update system for Yocto-based embedded Linux
"""

import os
import sys
import json
import hashlib
import tarfile
import logging
import argparse
import subprocess
import boto3
from botocore.exceptions import ClientError
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.backends import default_backend


class UpdateBundle:
    """Handles creation and verification of update bundles"""
    
    def __init__(self, config_path='/etc/embedded_updater/config.json'):
        """Initialize with config file path"""
        try:
            with open(config_path, 'r') as f:
                self.config = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            raise
            
        # S3 client setup
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=self.config.get('aws_access_key_id'),
            aws_secret_access_key=self.config.get('aws_secret_access_key'),
            region_name=self.config.get('aws_region')
        )
        
    def create_bundle(self, version, files, output_path):
        """
        Create an update bundle
        
        Args:
            version: Version string for this update
            files: Dictionary mapping destination paths to source files
            output_path: Where to write the bundle
        """
        logger.info(f"Creating update bundle v{version}")
        
        # Create a temporary directory for bundle contents
        temp_dir = f"/tmp/update_bundle_{version}"
        os.makedirs(temp_dir, exist_ok=True)
        
        # Create metadata
        metadata = {
            "version": version,
            "compatible_hardware": self.config.get("compatible_hardware", []),
            "files": {},
            "target_partition": "rootfs.1" if self.get_active_rootfs() == "rootfs.0" else "rootfs.0"
        }
        
        # Add files to the bundle
        for dest_path, source_path in files.items():
            if not os.path.exists(source_path):
                raise FileNotFoundError(f"Source file not found: {source_path}")
                
            # Calculate file hash
            sha256 = hashlib.sha256()
            with open(source_path, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b''):
                    sha256.update(chunk)
            file_hash = sha256.hexdigest()
            
            # Copy file to temp dir
            dest_name = os.path.basename(dest_path)
            dest_in_bundle = os.path.join(temp_dir, dest_name)
            subprocess.run(['cp', source_path, dest_in_bundle], check=True)
            
            # Add to metadata
            metadata["files"][dest_path] = {
                "hash": file_hash,
                "size": os.path.getsize(source_path),
                "bundle_path": dest_name
            }
        
        # Write metadata to bundle
        with open(os.path.join(temp_dir, "metadata.json"), 'w') as f:
            json.dump(metadata, f, indent=2)
        
        # Sign the metadata
        self._sign_metadata(temp_dir)
        
        # Create tar archive
        with tarfile.open(output_path, "w:gz") as tar:
            tar.add(temp_dir, arcname=os.path.basename(temp_dir))
        
        logger.info(f"Bundle created at {output_path}")
        
        # Clean up temp dir
        subprocess.run(['rm', '-rf', temp_dir], check=True)
        
        return output_path
    
    def _sign_metadata(self, bundle_dir):
        """Sign the metadata file in the bundle"""
        metadata_path = os.path.join(bundle_dir, "metadata.json")
        
        # Load private key
        try:
            with open(self.config["signing_key_path"], "rb") as key_file:
                private_key = serialization.load_pem_private_key(
                    key_file.read(),
                    password=None,
                    backend=default_backend()
                )
        except Exception as e:
            logger.error(f"Failed to load signing key: {e}")
            raise
        
        # Sign the metadata file
        with open(metadata_path, "rb") as f:
            metadata_bytes = f.read()
            
        signature = private_key.sign(
            metadata_bytes,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )
        
        # Write signature to file
        with open(os.path.join(bundle_dir, "metadata.sig"), "wb") as f:
            f.write(signature)
    
    def upload_to_s3(self, bundle_path, version):
        """Upload a bundle to S3"""
        try:
            bucket = self.config.get("s3_bucket")
            key = f"updates/{version}/{os.path.basename(bundle_path)}"
            
            logger.info(f"Uploading bundle to s3://{bucket}/{key}")
            self.s3_client.upload_file(bundle_path, bucket, key)
            
            # Create a latest.json pointer to this version
            latest = {"latest_version": version, "bundle_path": key}
            latest_json = json.dumps(latest).encode('utf-8')
            
            self.s3_client.put_object(
                Bucket=bucket,
                Key="updates/latest.json",
                Body=latest_json,
                ContentType="application/json"
            )
            
            logger.info(f"Upload complete and latest.json updated")
            return f"s3://{bucket}/{key}"
        except ClientError as e:
            logger.error(f"S3 upload failed: {e}")
            raise
    
    def get_active_rootfs(self):
        """Get currently active rootfs partition"""
        # This would need to be adapted to your specific setup
        try:
            result = subprocess.run(
                ["fw_printenv", "active_rootfs"],
                capture_output=True,
                text=True,
                check=True
            )
            active = result.stdout.strip().split("=")[1]
            return active
        except Exception as e:
            logger.error(f"Failed to determine active rootfs: {e}")
            # Default to rootfs.0 if we can't determine
            return "rootfs.0"


class UpdateClient:
    """Client that runs on the device to download and apply updates"""
    
    def __init__(self, config_path='/etc/embedded_updater/config.json'):
        """Initialize with config file path"""
        try:
            with open(config_path, 'r') as f:
                self.config = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            raise
            
        # S3 client setup
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=self.config.get('aws_access_key_id'),
            aws_secret_access_key=self.config.get('aws_secret_access_key'),
            region_name=self.config.get('aws_region')
        )
        
        # Load public key for verification
        try:
            with open(self.config["verification_key_path"], "rb") as key_file:
                self.public_key = serialization.load_pem_public_key(
                    key_file.read(),
                    backend=default_backend()
                )
        except Exception as e:
            logger.error(f"Failed to load verification key: {e}")
            raise
    
    def check_for_updates(self):
        """Check if updates are available"""
        try:
            bucket = self.config.get("s3_bucket")
            latest_obj = self.s3_client.get_object(
                Bucket=bucket,
                Key="updates/latest.json"
            )
            latest = json.loads(latest_obj['Body'].read().decode('utf-8'))
            
            # Compare with current version
            current_version = self.get_current_version()
            if latest["latest_version"] != current_version:
                logger.info(f"Update available: {current_version} -> {latest['latest_version']}")
                return latest
            else:
                logger.info("System is up to date")
                return None
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                logger.warning("No latest.json found in S3")
                return None
            else:
                logger.error(f"Error checking for updates: {e}")
                raise
    
    def download_update(self, update_info):
        """Download update bundle from S3"""
        try:
            bucket = self.config.get("s3_bucket")
            key = update_info["bundle_path"]
            local_path = f"/tmp/{os.path.basename(key)}"
            
            logger.info(f"Downloading s3://{bucket}/{key} to {local_path}")
            self.s3_client.download_file(bucket, key, local_path)
            
            return local_path
        except ClientError as e:
            logger.error(f"Download failed: {e}")
            raise
    
    def verify_bundle(self, bundle_path):
        """Verify bundle signature"""
        # Extract the bundle
        temp_dir = f"/tmp/verify_{os.path.basename(bundle_path).replace('.tar.gz', '')}"
        os.makedirs(temp_dir, exist_ok=True)
        
        try:
            # Extract the bundle
            with tarfile.open(bundle_path, "r:gz") as tar:
                tar.extractall(path="/tmp")
                
            bundle_dir = os.path.join("/tmp", os.path.basename(bundle_path).replace('.tar.gz', ''))
            
            # Read metadata and signature
            with open(os.path.join(bundle_dir, "metadata.json"), "rb") as f:
                metadata_bytes = f.read()
                
            with open(os.path.join(bundle_dir, "metadata.sig"), "rb") as f:
                signature = f.read()
            
            # Verify signature
            try:
                self.public_key.verify(
                    signature,
                    metadata_bytes,
                    padding.PSS(
                        mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.MAX_LENGTH
                    ),
                    hashes.SHA256()
                )
                logger.info("Bundle signature verified successfully")
                
                # Load metadata
                metadata = json.loads(metadata_bytes.decode('utf-8'))
                return bundle_dir, metadata
            except Exception as e:
                logger.error(f"Signature verification failed: {e}")
                return None, None
                
        except Exception as e:
            logger.error(f"Bundle verification failed: {e}")
            subprocess.run(['rm', '-rf', temp_dir], check=True)
            return None, None
    
    def apply_update(self, bundle_dir, metadata):
        """Apply the update to the inactive partition"""
        target_partition = metadata["target_partition"]
        logger.info(f"Applying update to {target_partition}")
        
        try:
            # Mount the target partition
            mount_point = f"/mnt/{target_partition}"
            os.makedirs(mount_point, exist_ok=True)
            
            # Determine device path for the target partition
            # This would need to be adapted to your specific setup
            if target_partition == "rootfs.0":
                device = "/dev/mmcblk0p2"
            else:
                device = "/dev/mmcblk0p3"
                
            # Mount the partition
            subprocess.run(
                ["mount", device, mount_point],
                check=True
            )
            
            # Copy files to the target partition
            for dest_path, file_info in metadata["files"].items():
                # Create directory structure if needed
                dest_dir = os.path.dirname(os.path.join(mount_point, dest_path.lstrip('/')))
                os.makedirs(dest_dir, exist_ok=True)
                
                # Copy file
                source_in_bundle = os.path.join(bundle_dir, file_info["bundle_path"])
                target_path = os.path.join(mount_point, dest_path.lstrip('/'))
                
                subprocess.run(
                    ["cp", source_in_bundle, target_path],
                    check=True
                )
                
                logger.info(f"Copied {source_in_bundle} to {target_path}")
            
            # Sync and unmount
            subprocess.run(["sync"], check=True)
            subprocess.run(["umount", mount_point], check=True)
            
            # Update U-Boot environment variables
            subprocess.run(
                ["fw_setenv", "upgrade_available", "1"],
                check=True
            )
            subprocess.run(
                ["fw_setenv", "target_rootfs", target_partition],
                check=True
            )
            subprocess.run(
                ["fw_setenv", "target_version", metadata["version"]],
                check=True
            )
            
            logger.info("Update applied successfully. Reboot to apply.")
            return True
        except Exception as e:
            logger.error(f"Failed to apply update: {e}")
            # Attempt to unmount if mounted
            try:
                subprocess.run(["umount", mount_point], check=False)
            except:
                pass
            return False
    
    def get_current_version(self):
        """Get the current system version"""
        try:
            with open("/etc/os-release", "r") as f:
                for line in f:
                    if line.startswith("VERSION="):
                        return line.split("=")[1].strip().strip('"')
        except Exception as e:
            logger.error(f"Failed to get current version: {e}")
            return "unknown"


class BootControl:
    """Handles boot control and U-Boot integration"""
    
    @staticmethod
    def mark_boot_successful():
        """Mark the current boot as successful"""
        try:
            # Reset the boot counter
            subprocess.run(
                ["fw_setenv", "boot_count", "0"],
                check=True
            )
            
            # If we're booting from the target partition after an upgrade,
            # mark the upgrade as complete
            result = subprocess.run(
                ["fw_printenv", "upgrade_available"],
                capture_output=True,
                text=True,
                check=True
            )
            
            if "upgrade_available=1" in result.stdout:
                # Update active_rootfs to current rootfs
                result = subprocess.run(
                    ["fw_printenv", "target_rootfs"],
                    capture_output=True,
                    text=True,
                    check=True
                )
                target_rootfs = result.stdout.strip().split("=")[1]
                
                subprocess.run(
                    ["fw_setenv", "active_rootfs", target_rootfs],
                    check=True
                )
                
                # Clear upgrade flag
                subprocess.run(
                    ["fw_setenv", "upgrade_available", "0"],
                    check=True
                )
                
                logger.info(f"Boot successful, updated active rootfs to {target_rootfs}")
            else:
                logger.info("Boot successful")
                
            return True
        except Exception as e:
            logger.error(f"Failed to mark boot successful: {e}")
            return False


def generate_keys():
    """Generate signing and verification keys"""
    # Generate private key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )
    
    # Get public key
    public_key = private_key.public_key()
    
    # Serialize private key
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    
    # Serialize public key
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    
    # Write to files
    os.makedirs('/etc/embedded_updater', exist_ok=True)
    
    with open('/etc/embedded_updater/private_key.pem', 'wb') as f:
        f.write(private_pem)
    
    with open('/etc/embedded_updater/public_key.pem', 'wb') as f:
        f.write(public_pem)
    
    os.chmod('/etc/embedded_updater/private_key.pem', 0o600)
    os.chmod('/etc/embedded_updater/public_key.pem', 0o644)
    
    print("Keys generated at:")
    print("  Private key: /etc/embedded_updater/private_key.pem")
    print("  Public key: /etc/embedded_updater/public_key.pem")


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description="Embedded System Update Tool")
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    
    # Create bundle command
    create_parser = subparsers.add_parser("create", help="Create update bundle")
    create_parser.add_argument("--version", required=True, help="Version string")
    create_parser.add_argument("--output", required=True, help="Output bundle path")
    create_parser.add_argument("--file", action="append", nargs=2, metavar=("DEST", "SRC"),
                              help="File to include in bundle (can be used multiple times)")
    create_parser.add_argument("--upload", action="store_true", help="Upload to S3 after creation")
    
    # Check for updates command
    subparsers.add_parser("check", help="Check for updates")
    
    # Apply update command
    apply_parser = subparsers.add_parser("apply", help="Apply available update")
    apply_parser.add_argument("--bundle", help="Local bundle path (optional)")
    
    # Boot success command
    subparsers.add_parser("boot-success", help="Mark boot as successful")
    
    # Generate keys command
    subparsers.add_parser("generate-keys", help="Generate signing and verification keys")
    
    args = parser.parse_args()
    
    if args.command == "create":
        # Convert --file arguments to dictionary
        files = {}
        if args.file:
            for dest, src in args.file:
                files[dest] = src
        
        updater = UpdateBundle()
        bundle_path = updater.create_bundle(args.version, files, args.output)
        
        if args.upload:
            s3_path = updater.upload_to_s3(bundle_path, args.version)
            print(f"Bundle uploaded to {s3_path}")
        else:
            print(f"Bundle created at {bundle_path}")
    
    elif args.command == "check":
        client = UpdateClient()
        update_info = client.check_for_updates()
        
        if update_info:
            print(f"Update available: {update_info['latest_version']}")
            print(f"Bundle path: {update_info['bundle_path']}")
        else:
            print("No updates available")
    
    elif args.command == "apply":
        client = UpdateClient()
        
        if args.bundle:
            # Use local bundle
            bundle_path = args.bundle
        else:
            # Check for updates and download
            update_info = client.check_for_updates()
            if not update_info:
                print("No updates available")
                return 0
                
            bundle_path = client.download_update(update_info)
        
        bundle_dir, metadata = client.verify_bundle(bundle_path)
        if not bundle_dir:
            print("Bundle verification failed")
            return 1
            
        success = client.apply_update(bundle_dir, metadata)
        if success:
            print("Update applied successfully. Reboot to apply.")
            return 0
        else:
            print("Update failed")
            return 1
    
    elif args.command == "boot-success":
        success = BootControl.mark_boot_successful()
        if success:
            print("Boot marked as successful")
            return 0
        else:
            print("Failed to mark boot as successful")
            return 1
    
    elif args.command == "generate-keys":
        generate_keys()
    
    else:
        parser.print_help()


if __name__ == "__main__":
    # If user has permission to write to /var/log, use it, otherwise use the current directory
    LOG_DIR = "/var/log/" if os.access("/var/log/", os.W_OK) else os.getcwd()
    LOG_PATH = os.path.join(LOG_DIR, "embedded_updater.log")
    # print("Logging to", LOG_PATH)

    # Set up logging
    logging.basicConfig(level=logging.INFO, 
                       format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                       handlers=[logging.FileHandler(LOG_PATH),
                                 logging.StreamHandler()])
    logger = logging.getLogger(__name__)

    sys.exit(main())
