import subprocess
import json
import os
import platform
from typing import Dict, List, Optional, Any
from rich.console import Console
from rich.prompt import Confirm

console = Console()

class EnvironmentDetector:
    """Detects and configures environment-specific settings for OPAL."""
    
    def __init__(self):
        self.environment_info = {}
        self.recommendations = []
        self.auto_fixes = []
        
    def detect_environment(self) -> Dict[str, Any]:
        """Detect the current environment and return comprehensive information."""
        console.print("[cyan]🔍 Detecting environment...[/cyan]")
        
        # Detect operating system
        self._detect_os()
        
        # Detect cloud provider
        self._detect_cloud_provider()
        
        # Detect security systems
        self._detect_security_systems()
        
        # Detect container runtime
        self._detect_container_runtime()
        
        # Detect network configuration
        self._detect_network_config()
        
        return self.environment_info
    
    def _detect_os(self):
        """Detect operating system details."""
        try:
            with open('/etc/os-release', 'r') as f:
                os_info = {}
                for line in f:
                    if '=' in line:
                        key, value = line.strip().split('=', 1)
                        os_info[key] = value.strip('"')
                self.environment_info['os'] = os_info
        except FileNotFoundError:
            self.environment_info['os'] = {
                'ID': 'unknown',
                'VERSION_ID': 'unknown',
                'PRETTY_NAME': platform.system() + ' ' + platform.release()
            }
    
    def _detect_cloud_provider(self):
        """Detect if running on a cloud provider."""
        # AWS detection
        try:
            aws_check = subprocess.run([
                'curl', '-s', '--max-time', '3', 
                'http://169.254.169.254/latest/meta-data/instance-id'
            ], capture_output=True, text=True)
            
            if aws_check.returncode == 0 and aws_check.stdout.strip():
                self.environment_info['cloud_provider'] = 'aws'
                self.environment_info['aws_instance_id'] = aws_check.stdout.strip()
                
                # Get additional AWS metadata
                self._get_aws_metadata()
                
        except Exception:
            pass
        
        # GCP detection
        try:
            gcp_check = subprocess.run([
                'curl', '-s', '--max-time', '3',
                '-H', 'Metadata-Flavor: Google',
                'http://metadata.google.internal/computeMetadata/v1/instance/id'
            ], capture_output=True, text=True)
            
            if gcp_check.returncode == 0 and gcp_check.stdout.strip():
                self.environment_info['cloud_provider'] = 'gcp'
                self.environment_info['gcp_instance_id'] = gcp_check.stdout.strip()
                
        except Exception:
            pass
        
        # Azure detection
        try:
            azure_check = subprocess.run([
                'curl', '-s', '--max-time', '3',
                '-H', 'Metadata: true',
                'http://169.254.169.254/metadata/instance/compute/vmId?api-version=2017-08-01&format=text'
            ], capture_output=True, text=True)
            
            if azure_check.returncode == 0 and azure_check.stdout.strip():
                self.environment_info['cloud_provider'] = 'azure'
                self.environment_info['azure_vm_id'] = azure_check.stdout.strip()
                
        except Exception:
            pass
    
    def _get_aws_metadata(self):
        """Get additional AWS metadata."""
        try:
            # Get availability zone
            az_result = subprocess.run([
                'curl', '-s', '--max-time', '3',
                'http://169.254.169.254/latest/meta-data/placement/availability-zone'
            ], capture_output=True, text=True)
            
            if az_result.returncode == 0:
                self.environment_info['aws_availability_zone'] = az_result.stdout.strip()
                
            # Get security groups
            sg_result = subprocess.run([
                'curl', '-s', '--max-time', '3',
                'http://169.254.169.254/latest/meta-data/security-groups'
            ], capture_output=True, text=True)
            
            if sg_result.returncode == 0:
                self.environment_info['aws_security_groups'] = sg_result.stdout.strip().split('\n')
                
            # Get instance type
            instance_type_result = subprocess.run([
                'curl', '-s', '--max-time', '3',
                'http://169.254.169.254/latest/meta-data/instance-type'
            ], capture_output=True, text=True)
            
            if instance_type_result.returncode == 0:
                self.environment_info['aws_instance_type'] = instance_type_result.stdout.strip()
                
        except Exception:
            pass
    
    def _detect_security_systems(self):
        """Detect security systems like SELinux, AppArmor."""
        # SELinux detection
        try:
            selinux_result = subprocess.run(['getenforce'], capture_output=True, text=True, check=True)
            self.environment_info['selinux'] = selinux_result.stdout.strip()
        except Exception:
            self.environment_info['selinux'] = 'Not available'
        
        # AppArmor detection
        try:
            apparmor_result = subprocess.run(['aa-status'], capture_output=True, text=True, check=True)
            self.environment_info['apparmor'] = 'enabled' if apparmor_result.returncode == 0 else 'disabled'
        except Exception:
            self.environment_info['apparmor'] = 'Not available'
        
        # Firewall detection
        try:
            ufw_result = subprocess.run(['ufw', 'status'], capture_output=True, text=True, check=True)
            self.environment_info['ufw'] = ufw_result.stdout.strip()
        except Exception:
            self.environment_info['ufw'] = 'Not available'
        
        try:
            firewalld_result = subprocess.run(['firewall-cmd', '--state'], capture_output=True, text=True, check=True)
            self.environment_info['firewalld'] = firewalld_result.stdout.strip()
        except Exception:
            self.environment_info['firewalld'] = 'Not available'
    
    def _detect_container_runtime(self):
        """Detect Docker and container runtime configuration."""
        try:
            docker_version = subprocess.run(['docker', '--version'], capture_output=True, text=True, check=True)
            self.environment_info['docker_version'] = docker_version.stdout.strip()
            
            # Get Docker info
            docker_info = subprocess.run(['docker', 'info', '--format', '{{json .}}'], capture_output=True, text=True, check=True)
            self.environment_info['docker_info'] = json.loads(docker_info.stdout)
            
        except Exception as e:
            self.environment_info['docker_error'] = str(e)
    
    def _detect_network_config(self):
        """Detect network configuration."""
        try:
            # Get network interfaces
            ip_result = subprocess.run(['ip', 'addr', 'show'], capture_output=True, text=True, check=True)
            self.environment_info['network_interfaces'] = ip_result.stdout
            
            # Get routing table
            route_result = subprocess.run(['ip', 'route'], capture_output=True, text=True, check=True)
            self.environment_info['routing_table'] = route_result.stdout
            
        except Exception:
            try:
                # Fallback to ifconfig
                ifconfig_result = subprocess.run(['ifconfig'], capture_output=True, text=True, check=True)
                self.environment_info['network_interfaces'] = ifconfig_result.stdout
            except Exception:
                self.environment_info['network_error'] = 'Cannot detect network configuration'
    
    def generate_recommendations(self) -> List[Dict[str, Any]]:
        """Generate environment-specific recommendations."""
        recommendations = []
        
        # AWS recommendations
        if self.environment_info.get('cloud_provider') == 'aws':
            recommendations.extend(self._get_aws_recommendations())
        
        # SELinux recommendations
        if self.environment_info.get('selinux') == 'Enforcing':
            recommendations.extend(self._get_selinux_recommendations())
        
        # Firewall recommendations
        if 'active' in self.environment_info.get('ufw', '').lower():
            recommendations.extend(self._get_firewall_recommendations())
        
        # Docker recommendations
        if 'docker_info' in self.environment_info:
            recommendations.extend(self._get_docker_recommendations())
        
        return recommendations
    
    def _get_aws_recommendations(self) -> List[Dict[str, Any]]:
        """Get AWS-specific recommendations."""
        recommendations = []
        
        recommendations.append({
            'category': 'aws_security_groups',
            'title': 'Configure AWS Security Groups',
            'description': 'Ensure your security groups allow necessary inbound traffic',
            'priority': 'high',
            'instructions': [
                '1. Go to AWS Console → EC2 → Security Groups',
                '2. Select your instance\'s security group',
                '3. Edit Inbound Rules:',
                '   • Add HTTPS (443) from your IP range',
                '   • Add HTTP (80) from your IP range (for Let\'s Encrypt)',
                '   • Add custom port if using non-standard port',
                '4. Save the rules'
            ],
            'aws_console_url': 'https://console.aws.amazon.com/ec2/v2/home#SecurityGroups:'
        })
        
        recommendations.append({
            'category': 'aws_nacl',
            'title': 'Check Network ACLs',
            'description': 'Verify Network ACLs allow traffic on required ports',
            'priority': 'medium',
            'instructions': [
                '1. Go to AWS Console → VPC → Network ACLs',
                '2. Find the NACL associated with your subnet',
                '3. Check Inbound Rules allow:',
                '   • HTTP (80) from 0.0.0.0/0',
                '   • HTTPS (443) from 0.0.0.0/0',
                '   • Custom ports as needed',
                '4. Check Outbound Rules allow response traffic'
            ],
            'aws_console_url': 'https://console.aws.amazon.com/vpc/home#acls:'
        })
        
        recommendations.append({
            'category': 'aws_instance',
            'title': 'Instance Configuration',
            'description': 'Optimize instance configuration for OPAL',
            'priority': 'low',
            'instructions': [
                '1. Ensure instance has at least 4GB RAM',
                '2. Check EBS volume has sufficient space (20GB+)',
                '3. Consider using instance types with enhanced networking',
                '4. Enable detailed monitoring if needed'
            ]
        })
        
        return recommendations
    
    def _get_selinux_recommendations(self) -> List[Dict[str, Any]]:
        """Get SELinux-specific recommendations."""
        recommendations = []
        
        recommendations.append({
            'category': 'selinux_config',
            'title': 'Configure SELinux for Docker',
            'description': 'Set SELinux booleans to allow Docker operations',
            'priority': 'high',
            'commands': [
                'sudo setsebool -P container_manage_cgroup on',
                'sudo setsebool -P container_connect_any on',
                'sudo setsebool -P container_use_cephfs on'
            ],
            'instructions': [
                '1. Run the commands above to configure SELinux',
                '2. Restart Docker: sudo systemctl restart docker',
                '3. Test container connectivity',
                '4. If issues persist, check audit logs: sudo ausearch -m avc -ts recent'
            ],
            'alternative': 'For development only: sudo setenforce 0'
        })
        
        return recommendations
    
    def _get_firewall_recommendations(self) -> List[Dict[str, Any]]:
        """Get firewall-specific recommendations."""
        recommendations = []
        
        recommendations.append({
            'category': 'firewall_config',
            'title': 'Configure Firewall for Docker',
            'description': 'Allow Docker traffic through the firewall',
            'priority': 'high',
            'commands': [
                'sudo ufw allow from 172.16.0.0/12',
                'sudo ufw allow out on docker0',
                'sudo ufw allow in on docker0'
            ],
            'instructions': [
                '1. Allow Docker subnet traffic',
                '2. Allow traffic on Docker bridge interface',
                '3. Allow external access to your OPAL port',
                '4. Reload firewall: sudo ufw reload'
            ]
        })
        
        return recommendations
    
    def _get_docker_recommendations(self) -> List[Dict[str, Any]]:
        """Get Docker-specific recommendations."""
        recommendations = []
        
        docker_info = self.environment_info.get('docker_info', {})
        
        # Check Docker version
        docker_version = self.environment_info.get('docker_version', '')
        if docker_version:
            # Extract version number
            import re
            version_match = re.search(r'(\d+)\.(\d+)\.(\d+)', docker_version)
            if version_match:
                major, minor, patch = map(int, version_match.groups())
                if major < 20 or (major == 20 and minor < 10):
                    recommendations.append({
                        'category': 'docker_version',
                        'title': 'Upgrade Docker Version',
                        'description': f'Current Docker version ({docker_version}) is older than recommended',
                        'priority': 'medium',
                        'instructions': [
                            '1. Check current version: docker --version',
                            '2. Update Docker: sudo apt-get update && sudo apt-get install docker-ce',
                            '3. Restart Docker: sudo systemctl restart docker',
                            '4. Verify: docker --version'
                        ]
                    })
        
        # Check storage driver
        storage_driver = docker_info.get('Driver', '')
        if storage_driver == 'devicemapper':
            recommendations.append({
                'category': 'docker_storage',
                'title': 'Consider Storage Driver Change',
                'description': 'devicemapper is not recommended for production',
                'priority': 'low',
                'instructions': [
                    '1. Stop Docker: sudo systemctl stop docker',
                    '2. Edit /etc/docker/daemon.json to use overlay2',
                    '3. Remove existing containers and images (backup first)',
                    '4. Start Docker: sudo systemctl start docker'
                ]
            })
        
        return recommendations
    
    def apply_environment_fixes(self, fixes: List[Dict[str, Any]]) -> None:
        """Apply automatic environment fixes."""
        console.print("\n[bold cyan]🔧 Applying Environment Fixes[/bold cyan]")
        
        for fix in fixes:
            if fix.get('automatic', False):
                console.print(f"\n[yellow]Applying: {fix['title']}[/yellow]")
                
                if Confirm.ask(f"Apply {fix['title']}?"):
                    try:
                        for command in fix.get('commands', []):
                            console.print(f"[dim]Running: {command}[/dim]")
                            subprocess.run(command, shell=True, check=True)
                        console.print("[green]✓ Fix applied successfully[/green]")
                    except subprocess.CalledProcessError as e:
                        console.print(f"[red]✗ Fix failed: {e}[/red]")
                        
                        # Offer manual instructions
                        if 'instructions' in fix:
                            console.print("[yellow]Manual instructions:[/yellow]")
                            for instruction in fix['instructions']:
                                console.print(f"  {instruction}")
            else:
                console.print(f"\n[yellow]Manual fix required: {fix['title']}[/yellow]")
                console.print(f"[dim]{fix['description']}[/dim]")
                
                if 'instructions' in fix:
                    console.print("[cyan]Instructions:[/cyan]")
                    for instruction in fix['instructions']:
                        console.print(f"  {instruction}")
                        
                if 'commands' in fix:
                    console.print("[cyan]Commands to run:[/cyan]")
                    for command in fix['commands']:
                        console.print(f"  {command}")
    
    def auto_configure_for_environment(self) -> bool:
        """Automatically configure OPAL for the detected environment."""
        console.print("[bold cyan]🚀 Auto-configuring for environment...[/bold cyan]")
        
        success = True
        
        # AWS configuration
        if self.environment_info.get('cloud_provider') == 'aws':
            success &= self._configure_for_aws()
        
        # SELinux configuration
        if self.environment_info.get('selinux') == 'Enforcing':
            success &= self._configure_for_selinux()
        
        # Firewall configuration
        if 'active' in self.environment_info.get('ufw', '').lower():
            success &= self._configure_for_firewall()
        
        return success
    
    def _configure_for_aws(self) -> bool:
        """Configure OPAL for AWS environment."""
        console.print("[cyan]Configuring for AWS environment...[/cyan]")
        
        try:
            # Check if we need to configure Docker daemon for AWS
            daemon_config = {}
            daemon_config_path = '/etc/docker/daemon.json'
            
            if os.path.exists(daemon_config_path):
                with open(daemon_config_path, 'r') as f:
                    daemon_config = json.load(f)
            
            # Configure DNS for AWS
            if 'dns' not in daemon_config:
                daemon_config['dns'] = ['169.254.169.253', '8.8.8.8']
                
                if Confirm.ask("Configure Docker DNS for AWS?"):
                    with open(daemon_config_path, 'w') as f:
                        json.dump(daemon_config, f, indent=2)
                    
                    subprocess.run(['sudo', 'systemctl', 'restart', 'docker'], check=True)
                    console.print("[green]✓ Docker DNS configured for AWS[/green]")
            
            return True
            
        except Exception as e:
            console.print(f"[red]✗ Failed to configure for AWS: {e}[/red]")
            return False
    
    def _configure_for_selinux(self) -> bool:
        """Configure OPAL for SELinux environment."""
        console.print("[cyan]Configuring for SELinux environment...[/cyan]")
        
        try:
            if Confirm.ask("Configure SELinux for Docker?"):
                commands = [
                    'sudo setsebool -P container_manage_cgroup on',
                    'sudo setsebool -P container_connect_any on',
                    'sudo setsebool -P container_use_cephfs on'
                ]
                
                for cmd in commands:
                    console.print(f"[dim]Running: {cmd}[/dim]")
                    subprocess.run(cmd, shell=True, check=True)
                
                console.print("[green]✓ SELinux configured for Docker[/green]")
            
            return True
            
        except Exception as e:
            console.print(f"[red]✗ Failed to configure SELinux: {e}[/red]")
            return False
    
    def _configure_for_firewall(self) -> bool:
        """Configure OPAL for firewall environment."""
        console.print("[cyan]Configuring for firewall environment...[/cyan]")
        
        try:
            if Confirm.ask("Configure firewall for Docker?"):
                commands = [
                    'sudo ufw allow from 172.16.0.0/12',
                    'sudo ufw allow out on docker0',
                    'sudo ufw allow in on docker0'
                ]
                
                for cmd in commands:
                    console.print(f"[dim]Running: {cmd}[/dim]")
                    subprocess.run(cmd, shell=True, check=True)
                
                console.print("[green]✓ Firewall configured for Docker[/green]")
            
            return True
            
        except Exception as e:
            console.print(f"[red]✗ Failed to configure firewall: {e}[/red]")
            return False 