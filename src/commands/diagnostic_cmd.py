import click
import subprocess
import json
import socket
import sys
import time
import platform
import re
import os
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.progress import Progress, SpinnerColumn, TextColumn

from src.core.config_manager import load_config, CONFIG_FILE
from src.core.docker_manager import get_docker_compose_command, run_docker_compose

console = Console()

class NetworkDiagnostic:
    def __init__(self):
        self.config = load_config() if CONFIG_FILE.exists() else {}
        self.stack_name = self.config.get('stack_name', 'easy-opal')
        self.issues = []
        self.fixes = []
        self.environment_info = {}
        
    def collect_environment_info(self):
        """Collect comprehensive environment information."""
        console.print("\n[bold cyan]🔍 Collecting Environment Information[/bold cyan]")
        
        # Detect macOS early
        if platform.system() == 'Darwin':
            console.print("[yellow]⚠️  macOS detected - This diagnostic tool is designed for Linux environments[/yellow]")
            console.print("[yellow]   OPAL is typically deployed on Linux servers (AWS, Ubuntu, etc.)[/yellow]")
            self.environment_info['os'] = {
                'ID': 'macos',
                'VERSION_ID': platform.mac_ver()[0],
                'PRETTY_NAME': f'macOS {platform.mac_ver()[0]}'
            }
            self.environment_info['macos'] = True
            return
            
        # OS Information (Linux)
        try:
            with open('/etc/os-release', 'r') as f:
                os_info = {}
                for line in f:
                    if '=' in line:
                        key, value = line.strip().split('=', 1)
                        os_info[key] = value.strip('"')
                self.environment_info['os'] = os_info
        except FileNotFoundError:
            self.environment_info['os'] = {'ID': 'unknown', 'VERSION_ID': 'unknown'}
        
        # Docker Information
        try:
            docker_version = subprocess.run(['docker', '--version'], capture_output=True, text=True, check=True)
            self.environment_info['docker_version'] = docker_version.stdout.strip()
            
            docker_info = subprocess.run(['docker', 'info', '--format', '{{json .}}'], capture_output=True, text=True, check=True)
            docker_info_data = json.loads(docker_info.stdout)
            self.environment_info['docker_info'] = docker_info_data
        except Exception as e:
            self.environment_info['docker_error'] = str(e)
        
        # Network Information
        try:
            ip_addr = subprocess.run(['ip', 'addr', 'show'], capture_output=True, text=True, check=True)
            self.environment_info['ip_addr'] = ip_addr.stdout
        except Exception:
            try:
                ifconfig = subprocess.run(['ifconfig'], capture_output=True, text=True, check=True)
                self.environment_info['ifconfig'] = ifconfig.stdout
            except Exception:
                self.environment_info['network_error'] = "Could not retrieve network information"
        
        # Skip Linux-specific checks on macOS
        if self.environment_info.get('macos'):
            return
            
        # SELinux Status
        try:
            selinux_status = subprocess.run(['getenforce'], capture_output=True, text=True, check=True, timeout=5)
            self.environment_info['selinux'] = selinux_status.stdout.strip()
        except Exception:
            self.environment_info['selinux'] = 'Not available'
        
        # Firewall Status
        try:
            ufw_status = subprocess.run(['ufw', 'status'], capture_output=True, text=True, check=True, timeout=5)
            self.environment_info['ufw'] = ufw_status.stdout.strip()
        except Exception:
            self.environment_info['ufw'] = 'Not available'
        
        # iptables rules
        try:
            iptables = subprocess.run(['sudo', 'iptables', '-L', '-n'], capture_output=True, text=True, check=True, timeout=10)
            self.environment_info['iptables'] = iptables.stdout
        except Exception:
            self.environment_info['iptables'] = 'Not available (requires sudo)'
        
        # AWS metadata (if available)
        try:
            aws_metadata = subprocess.run(['curl', '-s', '--max-time', '3', 'http://169.254.169.254/latest/meta-data/instance-id'], capture_output=True, text=True, timeout=5)
            if aws_metadata.returncode == 0 and aws_metadata.stdout:
                self.environment_info['aws_instance_id'] = aws_metadata.stdout.strip()
                
                # Get AWS security groups
                aws_sg = subprocess.run(['curl', '-s', '--max-time', '3', 'http://169.254.169.254/latest/meta-data/security-groups'], capture_output=True, text=True, timeout=5)
                if aws_sg.returncode == 0:
                    self.environment_info['aws_security_groups'] = aws_sg.stdout.strip().split('\n')
        except Exception:
            pass
        
        # Docker Compose Command
        compose_cmd = get_docker_compose_command()
        self.environment_info['compose_command'] = compose_cmd
        
        console.print("[green]✓ Environment information collected[/green]")
        
    def display_environment_summary(self):
        """Display a summary of the environment information."""
        console.print("\n[bold yellow]📊 Environment Summary[/bold yellow]")
        
        # Create environment table
        table = Table(title="System Information")
        table.add_column("Category", style="cyan")
        table.add_column("Value", style="green")
        
        # OS Information
        os_info = self.environment_info.get('os', {})
        table.add_row("Operating System", f"{os_info.get('PRETTY_NAME', 'Unknown')}")
        table.add_row("OS ID", f"{os_info.get('ID', 'Unknown')}")
        table.add_row("Version", f"{os_info.get('VERSION_ID', 'Unknown')}")
        
        # Docker Information
        if 'docker_version' in self.environment_info:
            table.add_row("Docker Version", self.environment_info['docker_version'])
        
        if 'docker_info' in self.environment_info:
            docker_info = self.environment_info['docker_info']
            table.add_row("Docker Root Dir", docker_info.get('DockerRootDir', 'Unknown'))
            table.add_row("Storage Driver", docker_info.get('Driver', 'Unknown'))
            table.add_row("Containers Running", str(docker_info.get('ContainersRunning', 0)))
        
        # Security
        table.add_row("SELinux Status", self.environment_info.get('selinux', 'Unknown'))
        
        # AWS
        if 'aws_instance_id' in self.environment_info:
            table.add_row("AWS Instance ID", self.environment_info['aws_instance_id'])
            if 'aws_security_groups' in self.environment_info:
                table.add_row("Security Groups", ", ".join(self.environment_info['aws_security_groups']))
        
        console.print(table)
        
    def check_docker_connectivity(self):
        """Check Docker daemon connectivity and basic functionality."""
        console.print("\n[bold cyan]🐳 Testing Docker Connectivity[/bold cyan]")
        
        # Test Docker daemon
        try:
            result = subprocess.run(['docker', 'info'], capture_output=True, text=True, check=True)
            console.print("[green]✓ Docker daemon is accessible[/green]")
        except subprocess.CalledProcessError:
            self.issues.append({
                'category': 'docker',
                'severity': 'critical',
                'title': 'Docker daemon not accessible',
                'description': 'The Docker daemon is not running or not accessible.',
                'solution': 'Start Docker daemon: sudo systemctl start docker'
            })
            console.print("[red]✗ Docker daemon not accessible[/red]")
            return False
        
        # Test Docker Compose
        compose_cmd = get_docker_compose_command()
        if not compose_cmd:
            self.issues.append({
                'category': 'docker',
                'severity': 'critical',
                'title': 'Docker Compose not available',
                'description': 'Docker Compose is not installed or not accessible.',
                'solution': 'Install Docker Compose: sudo apt-get install docker-compose-plugin'
            })
            console.print("[red]✗ Docker Compose not available[/red]")
            return False
        
        console.print(f"[green]✓ Docker Compose available: {' '.join(compose_cmd)}[/green]")
        return True
        
    def check_container_status(self):
        """Check the status of all containers in the stack."""
        console.print("\n[bold cyan]📦 Checking Container Status[/bold cyan]")
        
        try:
            # Get container status
            result = subprocess.run(['docker', 'ps', '-a', '--filter', f'name={self.stack_name}', '--format', 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'], 
                                  capture_output=True, text=True, check=True)
            
            if result.stdout.strip():
                console.print("[green]Container Status:[/green]")
                console.print(result.stdout)
                
                # Check for unhealthy containers
                lines = result.stdout.split('\n')[1:]  # Skip header
                for line in lines:
                    if line.strip():
                        parts = line.split('\t')
                        if len(parts) >= 2:
                            name = parts[0]
                            status = parts[1]
                            if 'Exited' in status or 'Dead' in status:
                                self.issues.append({
                                    'category': 'container',
                                    'severity': 'high',
                                    'title': f'Container {name} not running',
                                    'description': f'Container {name} has status: {status}',
                                    'solution': f'Check logs: docker logs {name}'
                                })
            else:
                console.print("[yellow]⚠ No containers found for stack '{self.stack_name}'[/yellow]")
                self.issues.append({
                    'category': 'container',
                    'severity': 'medium',
                    'title': 'No containers running',
                    'description': f'No containers found for stack {self.stack_name}',
                    'solution': 'Start the stack: ./easy-opal up'
                })
                
        except Exception as e:
            self.issues.append({
                'category': 'container',
                'severity': 'high',
                'title': 'Cannot check container status',
                'description': f'Error checking container status: {str(e)}',
                'solution': 'Check Docker daemon and permissions'
            })
            
    def check_container_connectivity(self):
        """Test connectivity between containers."""
        console.print("\n[bold cyan]🔗 Testing Container Connectivity[/bold cyan]")
        
        # Get running containers
        try:
            result = subprocess.run(['docker', 'ps', '--filter', f'name={self.stack_name}', '--format', '{{.Names}}'], 
                                  capture_output=True, text=True, check=True, timeout=10)
            containers = [name.strip() for name in result.stdout.split('\n') if name.strip()]
            
            if not containers:
                console.print("[yellow]⚠ No running containers to test[/yellow]")
                console.print("[dim]💡 Start containers with: ./easy-opal up[/dim]")
                self.issues.append({
                    'category': 'connectivity',
                    'severity': 'medium',
                    'title': 'No containers running for connectivity tests',
                    'description': 'Container connectivity cannot be tested without running containers',
                    'solution': 'Start containers: ./easy-opal up'
                })
                return
                
            console.print(f"[green]Found {len(containers)} running containers[/green]")
            
            # Wait a moment for containers to be fully ready
            import time
            console.print("[dim]Waiting for containers to be ready...[/dim]")
            time.sleep(3)
            
            # Test Opal to Mongo connectivity
            opal_container = f"{self.stack_name}-opal"
            mongo_container = f"{self.stack_name}-mongo"
            
            if opal_container in containers and mongo_container in containers:
                self._test_container_connection(opal_container, 'mongo', 27017, 'MongoDB')
            elif opal_container in containers:
                console.print("[yellow]⚠ MongoDB container not found for connectivity test[/yellow]")
            elif mongo_container in containers:
                console.print("[yellow]⚠ OPAL container not found for connectivity test[/yellow]")
                
            # Test Opal to Rock connectivity
            rock_containers = [c for c in containers if c.startswith(f"{self.stack_name}-") and 'rock' in c]
            if opal_container in containers and rock_containers:
                for rock_container in rock_containers:
                    rock_name = rock_container.replace(f"{self.stack_name}-", "")
                    self._test_container_connection(opal_container, rock_name, 8085, 'Rock')
            elif opal_container in containers and not rock_containers:
                console.print("[yellow]⚠ No Rock containers found for connectivity test[/yellow]")
                
        except Exception as e:
            self.issues.append({
                'category': 'connectivity',
                'severity': 'high',
                'title': 'Cannot test container connectivity',
                'description': f'Error testing container connectivity: {str(e)}',
                'solution': 'Check Docker daemon and container status'
            })
            
    def _test_container_connection(self, from_container: str, to_host: str, port: int, service_name: str):
        """Test connection from one container to another."""
        try:
            # Check if the target container is actually running
            target_container = f"{self.stack_name}-{to_host}"
            container_check = subprocess.run(['docker', 'ps', '--filter', f'name={target_container}', '--quiet'], 
                                           capture_output=True, text=True, timeout=5)
            
            if not container_check.stdout.strip():
                console.print(f"[yellow]⚠ {service_name} container ({target_container}) is not running[/yellow]")
                self.issues.append({
                    'category': 'connectivity',
                    'severity': 'medium',
                    'title': f'{service_name} container not running',
                    'description': f'Cannot test connectivity to {service_name} because container is not running',
                    'solution': f'Start the {service_name} container: ./easy-opal up'
                })
                return
            
            # Test port connectivity using the most reliable methods first
            console.print(f"[cyan]Testing connectivity from {from_container} to {to_host}:{port} ({service_name})[/cyan]")
            
            # Method 1: Try socket test first (most reliable, always available in bash)
            socket_test = subprocess.run(['docker', 'exec', from_container, 'timeout', '5', 
                                        'bash', '-c', f'exec 6<>/dev/tcp/{to_host}/{port} && echo "Connection successful"'], 
                                       capture_output=True, text=True, timeout=10)
            
            if socket_test.returncode == 0:
                console.print(f"[green]✓ {from_container} can connect to {to_host}:{port} ({service_name})[/green]")
                return
            
            # Method 2: Try netcat if available
            nc_check = subprocess.run(['docker', 'exec', from_container, 'which', 'nc'], 
                                    capture_output=True, text=True, timeout=5)
            
            if nc_check.returncode == 0:
                nc_result = subprocess.run(['docker', 'exec', from_container, 'nc', '-z', '-w', '3', to_host, str(port)], 
                                         capture_output=True, text=True, timeout=10)
                
                if nc_result.returncode == 0:
                    console.print(f"[green]✓ {from_container} can connect to {to_host}:{port} ({service_name}) via netcat[/green]")
                    return
            
            # Method 3: Try curl for HTTP ports
            if port in [80, 443, 8080, 8443]:
                curl_check = subprocess.run(['docker', 'exec', from_container, 'which', 'curl'], 
                                          capture_output=True, text=True, timeout=5)
                
                if curl_check.returncode == 0:
                    protocol = "https" if port in [443, 8443] else "http"
                    curl_result = subprocess.run(['docker', 'exec', from_container, 'curl', '-s', '--max-time', '3', 
                                                f'{protocol}://{to_host}:{port}/'], 
                                               capture_output=True, text=True, timeout=10)
                    
                    if curl_result.returncode == 0 or "Connection refused" not in curl_result.stderr:
                        console.print(f"[green]✓ {from_container} can connect to {to_host}:{port} ({service_name}) via HTTP[/green]")
                        return
            
            # Method 4: Try telnet if available
            telnet_check = subprocess.run(['docker', 'exec', from_container, 'which', 'telnet'], 
                                        capture_output=True, text=True, timeout=5)
            
            if telnet_check.returncode == 0:
                telnet_test = subprocess.run(['docker', 'exec', from_container, 'timeout', '3', 
                                            'bash', '-c', f'echo "" | telnet {to_host} {port}'], 
                                           capture_output=True, text=True, timeout=10)
                
                if telnet_test.returncode == 0 or "Connected" in telnet_test.stdout:
                    console.print(f"[green]✓ {from_container} can connect to {to_host}:{port} ({service_name}) via telnet[/green]")
                    return
            
            # If all methods fail, report the issue
            console.print(f"[red]✗ {from_container} cannot connect to {to_host}:{port} ({service_name})[/red]")
            console.print(f"[dim]   Attempted multiple connection methods[/dim]")
            
            # Check if the service is actually listening
            listen_check = subprocess.run(['docker', 'exec', target_container, 'ss', '-tlnp'], 
                                        capture_output=True, text=True, timeout=5)
            
            if listen_check.returncode == 0 and f":{port}" in listen_check.stdout:
                console.print(f"[yellow]⚠ {service_name} is listening on port {port} but not accessible[/yellow]")
                self.issues.append({
                    'category': 'connectivity',
                    'severity': 'high',
                    'title': f'Cannot connect to {service_name} port',
                    'description': f'Container {from_container} cannot connect to {to_host}:{port}, but service is listening',
                    'solution': f'Check firewall rules and Docker network configuration'
                })
            else:
                console.print(f"[red]✗ {service_name} may not be listening on port {port}[/red]")
                self.issues.append({
                    'category': 'connectivity',
                    'severity': 'high',
                    'title': f'{service_name} not listening on expected port',
                    'description': f'{service_name} service is not listening on port {port}',
                    'solution': f'Check {service_name} service configuration and startup logs'
                })
                
        except subprocess.TimeoutExpired:
            console.print(f"[red]✗ Connection test to {to_host}:{port} timed out[/red]")
            self.issues.append({
                'category': 'connectivity',
                'severity': 'high',
                'title': f'Connection timeout to {service_name}',
                'description': f'Connection test from {from_container} to {to_host}:{port} timed out',
                'solution': 'Check firewall rules and network configuration'
            })
        except Exception as e:
            console.print(f"[red]✗ Error testing connection to {to_host}:{port}: {str(e)}[/red]")
            self.issues.append({
                'category': 'connectivity',
                'severity': 'medium',
                'title': f'Error testing {service_name} connectivity',
                'description': f'Unexpected error during connectivity test: {str(e)}',
                'solution': 'Check container status and Docker daemon'
            })
            
    def check_selinux_issues(self):
        """Check for SELinux-related issues."""
        console.print("\n[bold cyan]🛡️ Checking SELinux Configuration[/bold cyan]")
        
        # Skip if on macOS
        if self.environment_info.get('macos'):
            console.print("[green]✓ SELinux not applicable on macOS[/green]")
            return
            
        selinux_status = self.environment_info.get('selinux', 'Unknown')
        
        if selinux_status == 'Enforcing':
            console.print("[yellow]⚠ SELinux is in Enforcing mode[/yellow]")
            
            # Check SELinux booleans for Docker
            self._check_selinux_booleans()
            
            # Check volume-specific SELinux issues
            self._check_selinux_volumes()
            
            # Check for Docker-related SELinux denials
            try:
                audit_result = subprocess.run(['sudo', 'ausearch', '-m', 'avc', '-ts', 'recent'], 
                                            capture_output=True, text=True, timeout=10)
                
                if audit_result.returncode == 0 and 'docker' in audit_result.stdout.lower():
                    console.print("[red]✗ SELinux denials detected for Docker[/red]")
                    
                    # Analyze specific denials
                    self._analyze_selinux_denials(audit_result.stdout)
                    
                    # Add more specific SELinux fixes
                    self.fixes.append({
                        'name': 'Configure SELinux for Docker',
                        'description': 'Configure SELinux to allow Docker operations',
                        'commands': [
                            'sudo setsebool -P container_manage_cgroup on',
                            'sudo setsebool -P container_connect_any on',
                            'sudo setsebool -P container_use_cephfs on'
                        ],
                        'automatic': False
                    })
                else:
                    console.print("[green]✓ No recent SELinux denials for Docker[/green]")
                    
            except Exception:
                console.print("[yellow]⚠ Cannot check SELinux audit logs (requires sudo)[/yellow]")
                
        elif selinux_status == 'Permissive':
            console.print("[green]✓ SELinux is in Permissive mode[/green]")
        elif selinux_status == 'Disabled':
            console.print("[green]✓ SELinux is disabled[/green]")
        else:
            console.print("[yellow]⚠ SELinux status unknown[/yellow]")
            
    def _check_selinux_booleans(self):
        """Check SELinux booleans relevant to Docker."""
        try:
            docker_booleans = [
                'container_manage_cgroup',
                'container_connect_any',
                'container_use_cephfs'
            ]
            
            for boolean in docker_booleans:
                result = subprocess.run(['sudo', 'getsebool', boolean], 
                                      capture_output=True, text=True, timeout=5)
                
                if result.returncode == 0:
                    status = result.stdout.strip()
                    if 'on' in status:
                        console.print(f"[green]✓ SELinux boolean {boolean} is enabled[/green]")
                    else:
                        console.print(f"[red]✗ SELinux boolean {boolean} is disabled[/red]")
                        self.issues.append({
                            'category': 'selinux',
                            'severity': 'high',
                            'title': f'SELinux boolean {boolean} disabled',
                            'description': f'SELinux boolean {boolean} must be enabled for Docker to work properly',
                            'solution': f'Enable boolean: sudo setsebool -P {boolean} on'
                        })
                        
        except Exception as e:
            console.print(f"[yellow]⚠ Cannot check SELinux booleans: {str(e)}[/yellow]")
            
    def _check_selinux_volumes(self):
        """Check SELinux contexts for Docker volumes."""
        try:
            console.print("[cyan]Checking SELinux volume contexts...[/cyan]")
            
            # Check Docker root directory context
            docker_root = self.environment_info.get('docker_info', {}).get('DockerRootDir', '/var/lib/docker')
            
            context_result = subprocess.run(['ls', '-Z', docker_root], 
                                          capture_output=True, text=True, timeout=5)
            
            if context_result.returncode == 0:
                context_output = context_result.stdout.strip()
                if 'container_file_t' in context_output or 'svirt_sandbox_file_t' in context_output:
                    console.print(f"[green]✓ Docker root has correct SELinux context[/green]")
                else:
                    console.print(f"[yellow]⚠ Docker root may have incorrect SELinux context[/yellow]")
                    console.print(f"[dim]Context: {context_output}[/dim]")
                    
                    self.issues.append({
                        'category': 'selinux',
                        'severity': 'medium',
                        'title': 'Docker root SELinux context issue',
                        'description': f'Docker root directory may have incorrect SELinux context',
                        'solution': f'Fix context: sudo restorecon -Rv {docker_root}'
                    })
                    
            # Check volume directories context
            volumes_dir = f"{docker_root}/volumes"
            if os.path.exists(volumes_dir):
                try:
                    volume_dirs = os.listdir(volumes_dir)
                    opal_volume_dirs = [d for d in volume_dirs if self.stack_name in d]
                    
                    for vol_dir in opal_volume_dirs[:3]:  # Check first 3 volumes
                        vol_path = os.path.join(volumes_dir, vol_dir)
                        vol_context = subprocess.run(['ls', '-Z', vol_path], 
                                                   capture_output=True, text=True, timeout=5)
                        
                        if vol_context.returncode == 0:
                            if 'container_file_t' not in vol_context.stdout:
                                console.print(f"[yellow]⚠ Volume {vol_dir} may have incorrect SELinux context[/yellow]")
                                self.issues.append({
                                    'category': 'selinux',
                                    'severity': 'medium',
                                    'title': f'Volume SELinux context issue',
                                    'description': f'Volume {vol_dir} may have incorrect SELinux context',
                                    'solution': f'Fix context: sudo restorecon -Rv {vol_path}'
                                })
                            else:
                                console.print(f"[green]✓ Volume {vol_dir} has correct SELinux context[/green]")
                                
                except PermissionError:
                    console.print("[yellow]⚠ Cannot check volume SELinux contexts (requires sudo)[/yellow]")
                    
        except Exception as e:
            console.print(f"[yellow]⚠ Cannot check SELinux volume contexts: {str(e)}[/yellow]")
            
    def _analyze_selinux_denials(self, audit_output: str):
        """Analyze specific SELinux denials and provide targeted solutions."""
        try:
            denials = audit_output.split('\n')
            
            volume_denials = [d for d in denials if 'write' in d and ('var/lib/docker' in d or 'volume' in d)]
            network_denials = [d for d in denials if 'connect' in d or 'bind' in d]
            
            if volume_denials:
                console.print("[red]✗ SELinux is blocking Docker volume operations[/red]")
                self.issues.append({
                    'category': 'selinux',
                    'severity': 'high',
                    'title': 'SELinux blocking Docker volume access',
                    'description': 'SELinux is preventing Docker from accessing volumes',
                    'solution': 'Fix volume contexts and enable container_manage_cgroup'
                })
                
            if network_denials:
                console.print("[red]✗ SELinux is blocking Docker network operations[/red]")
                self.issues.append({
                    'category': 'selinux',
                    'severity': 'high',
                    'title': 'SELinux blocking Docker network access',
                    'description': 'SELinux is preventing Docker containers from network access',
                    'solution': 'Enable container_connect_any: sudo setsebool -P container_connect_any on'
                })
                
        except Exception as e:
            console.print(f"[yellow]⚠ Cannot analyze SELinux denials: {str(e)}[/yellow]")
            
    def check_firewall_issues(self):
        """Check for firewall-related issues."""
        console.print("\n[bold cyan]🔥 Checking Firewall Configuration[/bold cyan]")
        
        # Check UFW
        ufw_status = self.environment_info.get('ufw', 'Unknown')
        if 'active' in ufw_status.lower():
            console.print("[yellow]⚠ UFW firewall is active[/yellow]")
            self.issues.append({
                'category': 'firewall',
                'severity': 'medium',
                'title': 'UFW firewall is active',
                'description': 'UFW firewall may be blocking Docker container communication.',
                'solution': 'Configure UFW to allow Docker: sudo ufw allow from 172.16.0.0/12'
            })
        else:
            console.print("[green]✓ UFW firewall is not active[/green]")
            
        # Check iptables rules
        iptables_output = self.environment_info.get('iptables', '')
        if iptables_output and iptables_output != 'Not available (requires sudo)':
            if 'DOCKER' in iptables_output:
                console.print("[green]✓ Docker iptables rules are present[/green]")
            else:
                console.print("[yellow]⚠ Docker iptables rules not found[/yellow]")
                self.issues.append({
                    'category': 'firewall',
                    'severity': 'medium',
                    'title': 'Docker iptables rules missing',
                    'description': 'Docker iptables rules are not properly configured.',
                    'solution': 'Restart Docker daemon: sudo systemctl restart docker'
                })
                
            # Check for overly restrictive FORWARD rules
            if 'FORWARD' in iptables_output and 'DROP' in iptables_output:
                console.print("[yellow]⚠ Restrictive FORWARD rules detected[/yellow]")
                self.fixes.append({
                    'name': 'Fix Docker FORWARD rules',
                    'description': 'Allow Docker bridge traffic through iptables',
                    'commands': [
                        'sudo iptables -I FORWARD -i docker0 -o docker0 -j ACCEPT',
                        'sudo iptables -I FORWARD -i docker0 -j ACCEPT'
                    ],
                    'automatic': False
                })
        else:
            console.print("[yellow]⚠ Cannot check iptables rules (requires sudo)[/yellow]")
            
    def check_aws_issues(self):
        """Check for AWS-specific networking issues."""
        console.print("\n[bold cyan]☁️ Checking AWS Configuration[/bold cyan]")
        
        if 'aws_instance_id' not in self.environment_info:
            console.print("[green]✓ Not running on AWS[/green]")
            return
            
        console.print(f"[yellow]⚠ Running on AWS instance: {self.environment_info['aws_instance_id']}[/yellow]")
        
        # Check security groups
        security_groups = self.environment_info.get('aws_security_groups', [])
        if security_groups:
            console.print(f"[cyan]Security Groups: {', '.join(security_groups)}[/cyan]")
            
        # Common AWS issues
        self.issues.append({
            'category': 'aws',
            'severity': 'medium',
            'title': 'AWS Security Group Configuration',
            'description': 'Ensure security groups allow necessary traffic for OPAL.',
            'solution': 'Check AWS Console: EC2 → Security Groups → Inbound Rules'
        })
        
        # Check if source/destination check is disabled (needed for NAT)
        console.print("[yellow]⚠ AWS-specific checks needed:[/yellow]")
        console.print("  • Security Groups must allow inbound traffic on configured ports")
        console.print("  • NACLs must allow traffic")
        console.print("  • Source/destination checks may need to be disabled for advanced networking")
        
    def check_docker_network_issues(self):
        """Check Docker network configuration issues."""
        console.print("\n[bold cyan]🌐 Checking Docker Network Configuration[/bold cyan]")
        
        try:
            # List networks
            networks_result = subprocess.run(['docker', 'network', 'ls'], capture_output=True, text=True, check=True)
            console.print("[green]✓ Docker networks:[/green]")
            for line in networks_result.stdout.split('\n')[1:]:  # Skip header
                if line.strip():
                    console.print(f"  {line}")
                    
            # Check default bridge network
            bridge_result = subprocess.run(['docker', 'network', 'inspect', 'bridge'], capture_output=True, text=True, check=True)
            bridge_info = json.loads(bridge_result.stdout)[0]
            
            ipam_config = bridge_info.get('IPAM', {}).get('Config', [])
            if ipam_config:
                subnet = ipam_config[0].get('Subnet', 'Unknown')
                console.print(f"[green]✓ Bridge network subnet: {subnet}[/green]")
            else:
                console.print("[yellow]⚠ Bridge network configuration unclear[/yellow]")
                
            # Check for network conflicts
            try:
                route_result = subprocess.run(['ip', 'route'], capture_output=True, text=True, check=True)
                if '172.17.0.0/16' in route_result.stdout:
                    console.print("[green]✓ Docker bridge route is present[/green]")
                else:
                    console.print("[yellow]⚠ Docker bridge route not found[/yellow]")
            except Exception:
                console.print("[yellow]⚠ Cannot check routing table[/yellow]")
                
        except Exception as e:
            self.issues.append({
                'category': 'docker_network',
                'severity': 'high',
                'title': 'Cannot inspect Docker networks',
                'description': f'Error inspecting Docker networks: {str(e)}',
                'solution': 'Check Docker daemon status and permissions'
            })
            
    def check_dns_issues(self):
        """Check DNS resolution issues."""
        console.print("\n[bold cyan]🔍 Checking DNS Resolution[/bold cyan]")
        
        try:
            # Test DNS resolution from host
            host_dns = subprocess.run(['nslookup', 'google.com'], capture_output=True, text=True, check=True)
            console.print("[green]✓ Host DNS resolution working[/green]")
            
            # Test DNS resolution from container
            containers = subprocess.run(['docker', 'ps', '--filter', f'name={self.stack_name}', '--format', '{{.Names}}'], 
                                      capture_output=True, text=True, check=True)
            container_list = [name.strip() for name in containers.stdout.split('\n') if name.strip()]
            
            if container_list:
                test_container = container_list[0]
                container_dns = subprocess.run(['docker', 'exec', test_container, 'nslookup', 'google.com'], 
                                             capture_output=True, text=True, timeout=10)
                
                if container_dns.returncode == 0:
                    console.print(f"[green]✓ Container DNS resolution working ({test_container})[/green]")
                else:
                    console.print(f"[red]✗ Container DNS resolution failed ({test_container})[/red]")
                    self.issues.append({
                        'category': 'dns',
                        'severity': 'high',
                        'title': 'Container DNS resolution failing',
                        'description': 'Containers cannot resolve DNS names',
                        'solution': 'Configure Docker daemon DNS: edit /etc/docker/daemon.json'
                    })
                    
                    self.fixes.append({
                        'name': 'Fix Docker DNS configuration',
                        'description': 'Configure Docker daemon to use public DNS servers',
                        'commands': [
                            'sudo mkdir -p /etc/docker',
                            'echo \'{"dns": ["8.8.8.8", "1.1.1.1"]}\' | sudo tee /etc/docker/daemon.json',
                            'sudo systemctl restart docker'
                        ],
                        'automatic': False
                    })
            else:
                console.print("[yellow]⚠ No containers available for DNS testing[/yellow]")
                
        except subprocess.TimeoutExpired:
            console.print("[red]✗ DNS resolution test timed out[/red]")
        except Exception as e:
            console.print(f"[yellow]⚠ DNS test failed: {str(e)}[/yellow]")
            
    def check_volume_issues(self):
        """Check Docker volume configuration and mounting issues."""
        console.print("\n[bold cyan]💾 Checking Volume Configuration[/bold cyan]")
        
        is_macos = self.environment_info.get('macos', False)
        
        try:
            # Check Docker volumes (works on both macOS and Linux)
            volumes_result = subprocess.run(['docker', 'volume', 'ls'], capture_output=True, text=True, check=True, timeout=10)
            console.print(f"[green]✓ Docker volumes:[/green]")
            
            # Get OPAL-specific volumes
            opal_volumes = []
            for line in volumes_result.stdout.split('\n')[1:]:  # Skip header
                if line.strip() and self.stack_name in line:
                    opal_volumes.append(line.strip())
            
            if opal_volumes:
                for volume in opal_volumes:
                    console.print(f"  {volume}")
                    
                # Check volume mounting in containers
                self._check_volume_mounts()
                
                # Check volume inspect details
                self._check_volume_details()
                
                # Check volume space usage (different approach for macOS)
                self._check_volume_space()
                
                # Check volume permissions (adapted for macOS)
                if not is_macos:
                    self._check_volume_permissions()
                else:
                    self._check_macos_volume_permissions()
                
                # Check volume persistence
                self._check_volume_persistence()
                
            else:
                console.print("[yellow]⚠ No OPAL volumes found[/yellow]")
                self.issues.append({
                    'category': 'volumes',
                    'severity': 'medium',
                    'title': 'No OPAL volumes found',
                    'description': 'Expected Docker volumes for OPAL stack not found',
                    'solution': 'Check if containers are properly configured with persistent volumes'
                })
                
        except Exception as e:
            console.print(f"[red]✗ Error checking volumes: {str(e)}[/red]")
            self.issues.append({
                'category': 'volumes',
                'severity': 'medium',
                'title': 'Cannot check volume configuration',
                'description': f'Error checking Docker volumes: {str(e)}',
                'solution': 'Check Docker daemon status and permissions'
            })

    def _check_volume_mounts(self):
        """Check if volumes are properly mounted in containers."""
        try:
            # Check running containers for volume mounts
            containers_result = subprocess.run(['docker', 'ps', '--filter', f'name={self.stack_name}', '--format', '{{.Names}}'], 
                                             capture_output=True, text=True, check=True, timeout=10)
            
            containers = [name.strip() for name in containers_result.stdout.split('\n') if name.strip()]
            
            for container in containers:
                if 'mongo' in container:
                    self._check_container_volume_mount(container, '/data/db', 'MongoDB data directory', requires_volume=True)
                elif 'opal' in container and 'nginx' not in container:
                    self._check_container_volume_mount(container, '/srv', 'OPAL data directory', requires_volume=True)
                elif 'rock' in container:
                    self._check_container_volume_mount(container, '/srv', 'Rock data directory', requires_volume=True)
                elif 'nginx' in container:
                    # Nginx is designed for dynamic configuration, persistent volumes are optional
                    self._check_container_volume_mount(container, '/srv', 'OPAL data directory', requires_volume=False)
                    
        except Exception as e:
            console.print(f"[yellow]⚠ Cannot check volume mounts: {str(e)}[/yellow]")

    def _check_container_volume_mount(self, container: str, mount_path: str, description: str, requires_volume: bool = True):
        """Check if a specific volume is mounted in a container."""
        try:
            # Check if the mount path exists and is writable
            test_result = subprocess.run(['docker', 'exec', container, 'test', '-w', mount_path], 
                                       capture_output=True, text=True, timeout=5)
            
            if test_result.returncode == 0:
                console.print(f"[green]✓ {container}: {description} mounted and writable[/green]")
                
                # Check if it's actually a volume (not just a directory)
                mount_info = subprocess.run(['docker', 'exec', container, 'mount'], 
                                          capture_output=True, text=True, timeout=5)
                
                if mount_info.returncode == 0 and mount_path in mount_info.stdout:
                    console.print(f"[green]✓ {container}: {description} is a proper volume mount[/green]")
                else:
                    if requires_volume:
                        console.print(f"[yellow]⚠ {container}: {description} may not be a volume mount[/yellow]")
                        self.issues.append({
                            'category': 'volumes',
                            'severity': 'medium',
                            'title': f'{description} not properly mounted',
                            'description': f'Path {mount_path} in {container} may not be a volume mount',
                            'solution': 'Check docker-compose.yml volume configuration'
                        })
                    else:
                        console.print(f"[green]✓ {container}: {description} is dynamically configured (no persistent volume needed)[/green]")
            else:
                console.print(f"[red]✗ {container}: {description} not writable or missing[/red]")
                self.issues.append({
                    'category': 'volumes',
                    'severity': 'high',
                    'title': f'{description} mount issue',
                    'description': f'Cannot write to {mount_path} in {container}',
                    'solution': 'Check volume permissions and SELinux contexts'
                })
                
        except Exception as e:
            console.print(f"[yellow]⚠ Cannot check mount {mount_path} in {container}: {str(e)}[/yellow]")

    def _check_volume_permissions(self):
        """Check volume permissions and ownership."""
        try:
            # Check Docker volume directory permissions
            docker_root = self.environment_info.get('docker_info', {}).get('DockerRootDir', '/var/lib/docker')
            volumes_dir = f"{docker_root}/volumes"
            
            if os.path.exists(volumes_dir):
                # Check if we can read volume directory
                try:
                    volume_dirs = os.listdir(volumes_dir)
                    opal_volume_dirs = [d for d in volume_dirs if self.stack_name in d]
                    
                    if opal_volume_dirs:
                        console.print(f"[green]✓ Found {len(opal_volume_dirs)} OPAL volume directories[/green]")
                        
                        # Check permissions on volume directories
                        for vol_dir in opal_volume_dirs:
                            vol_path = os.path.join(volumes_dir, vol_dir, '_data')
                            if os.path.exists(vol_path):
                                stat_info = os.stat(vol_path)
                                permissions = oct(stat_info.st_mode)[-3:]
                                console.print(f"[green]✓ Volume {vol_dir}: permissions {permissions}[/green]")
                                
                                # Check for common permission issues
                                if permissions < '755':
                                    self.issues.append({
                                        'category': 'volumes',
                                        'severity': 'medium',
                                        'title': 'Restrictive volume permissions',
                                        'description': f'Volume {vol_dir} has restrictive permissions ({permissions})',
                                        'solution': 'Consider adjusting volume permissions if containers cannot access data'
                                    })
                    else:
                        console.print("[yellow]⚠ No OPAL volume directories found[/yellow]")
                        
                except PermissionError:
                    console.print("[yellow]⚠ Cannot read volume directories (requires sudo)[/yellow]")
                    
            else:
                console.print(f"[yellow]⚠ Docker volumes directory not found at {volumes_dir}[/yellow]")
                
        except Exception as e:
            console.print(f"[yellow]⚠ Cannot check volume permissions: {str(e)}[/yellow]")

    def _check_volume_space(self):
        """Check volume space usage."""
        try:
            is_macos = self.environment_info.get('macos', False)
            
            if is_macos:
                # macOS approach: Check overall disk usage
                df_result = subprocess.run(['df', '-h', '/'], capture_output=True, text=True, check=True, timeout=5)
                
                if df_result.returncode == 0:
                    lines = df_result.stdout.split('\n')
                    if len(lines) > 1:
                        parts = lines[1].split()
                        if len(parts) >= 5:
                            used_percent = parts[4].rstrip('%')
                            console.print(f"[green]✓ System disk usage: {used_percent}%[/green]")
                            
                            if int(used_percent) > 85:
                                self.issues.append({
                                    'category': 'volumes',
                                    'severity': 'high',
                                    'title': 'High disk usage (macOS)',
                                    'description': f'System disk is {used_percent}% full',
                                    'solution': 'Free up disk space or clean Docker: docker system prune'
                                })
                            elif int(used_percent) > 70:
                                self.issues.append({
                                    'category': 'volumes',
                                    'severity': 'medium',
                                    'title': 'Moderate disk usage (macOS)',
                                    'description': f'System disk is {used_percent}% full',
                                    'solution': 'Monitor disk space and consider cleanup: docker system df'
                                })
                                
                # Also check Docker's disk usage
                docker_df = subprocess.run(['docker', 'system', 'df'], capture_output=True, text=True, check=True, timeout=10)
                if docker_df.returncode == 0:
                    console.print(f"[green]✓ Docker disk usage:[/green]")
                    lines = docker_df.stdout.split('\n')
                    for line in lines[1:]:  # Skip header
                        if line.strip() and 'Volumes' in line:
                            console.print(f"  {line}")
                            
            else:
                # Linux approach: Check Docker volume space usage
                docker_root = self.environment_info.get('docker_info', {}).get('DockerRootDir', '/var/lib/docker')
                
                df_result = subprocess.run(['df', '-h', docker_root], capture_output=True, text=True, check=True, timeout=5)
                
                if df_result.returncode == 0:
                    lines = df_result.stdout.split('\n')
                    if len(lines) > 1:
                        parts = lines[1].split()
                        if len(parts) >= 5:
                            used_percent = parts[4].rstrip('%')
                            console.print(f"[green]✓ Docker volumes disk usage: {used_percent}%[/green]")
                            
                            if int(used_percent) > 85:
                                self.issues.append({
                                    'category': 'volumes',
                                    'severity': 'high',
                                    'title': 'High disk usage on Docker volumes',
                                    'description': f'Docker volumes disk is {used_percent}% full',
                                    'solution': 'Free up disk space or clean unused volumes: docker volume prune'
                                })
                            elif int(used_percent) > 70:
                                self.issues.append({
                                    'category': 'volumes',
                                    'severity': 'medium',
                                    'title': 'Moderate disk usage on Docker volumes',
                                    'description': f'Docker volumes disk is {used_percent}% full',
                                    'solution': 'Monitor disk space and consider cleanup: docker system df'
                                })
                            
        except Exception as e:
            console.print(f"[yellow]⚠ Cannot check volume space: {str(e)}[/yellow]")

    def _check_volume_details(self):
        """Check detailed volume information using docker volume inspect."""
        try:
            console.print("[cyan]Checking volume details...[/cyan]")
            
            # Get OPAL-specific volumes
            volumes_result = subprocess.run(['docker', 'volume', 'ls', '--format', '{{.Name}}'], 
                                          capture_output=True, text=True, check=True, timeout=10)
            
            opal_volumes = [vol.strip() for vol in volumes_result.stdout.split('\n') 
                          if vol.strip() and self.stack_name in vol.strip()]
            
            for volume in opal_volumes:
                inspect_result = subprocess.run(['docker', 'volume', 'inspect', volume], 
                                              capture_output=True, text=True, check=True, timeout=10)
                
                if inspect_result.returncode == 0:
                    volume_info = json.loads(inspect_result.stdout)[0]
                    
                    # Check volume driver
                    driver = volume_info.get('Driver', 'unknown')
                    mountpoint = volume_info.get('Mountpoint', 'unknown')
                    
                    console.print(f"[green]✓ Volume {volume}: driver={driver}[/green]")
                    console.print(f"  [dim]Mountpoint: {mountpoint}[/dim]")
                    
                    # Check if mountpoint is accessible
                    if mountpoint != 'unknown':
                        try:
                            # Try to check if mountpoint exists and is accessible
                            if os.path.exists(mountpoint):
                                console.print(f"  [green]✓ Mountpoint accessible[/green]")
                                
                                # Check if it has any data
                                try:
                                    files = os.listdir(mountpoint)
                                    if files:
                                        console.print(f"  [green]✓ Contains data ({len(files)} items)[/green]")
                                    else:
                                        console.print(f"  [yellow]⚠ Mountpoint is empty[/yellow]")
                                except PermissionError:
                                    console.print(f"  [yellow]⚠ Cannot read mountpoint contents (permission denied)[/yellow]")
                            else:
                                is_macos = self.environment_info.get('macos', False)
                                if is_macos:
                                    console.print(f"  [yellow]⚠ Mountpoint not accessible (normal on macOS)[/yellow]")
                                    # Don't report this as an issue on macOS since it's expected behavior
                                else:
                                    console.print(f"  [yellow]⚠ Mountpoint not accessible[/yellow]")
                                    self.issues.append({
                                        'category': 'volumes',
                                        'severity': 'medium',
                                        'title': f'Volume mountpoint not accessible',
                                        'description': f'Volume {volume} mountpoint {mountpoint} is not accessible',
                                        'solution': 'Check Docker daemon and volume driver configuration'
                                    })
                        except Exception as e:
                            console.print(f"  [yellow]⚠ Cannot check mountpoint: {str(e)}[/yellow]")
                    
                    # Check for volume labels
                    labels = volume_info.get('Labels', {})
                    if labels:
                        console.print(f"  [dim]Labels: {labels}[/dim]")
                    
        except Exception as e:
            console.print(f"[yellow]⚠ Cannot check volume details: {str(e)}[/yellow]")

    def _check_macos_volume_permissions(self):
        """Check volume permissions on macOS."""
        try:
            console.print("[cyan]Checking macOS volume permissions...[/cyan]")
            
            # Check running containers for volume access
            containers_result = subprocess.run(['docker', 'ps', '--filter', f'name={self.stack_name}', '--format', '{{.Names}}'], 
                                             capture_output=True, text=True, check=True, timeout=10)
            
            containers = [name.strip() for name in containers_result.stdout.split('\n') if name.strip()]
            
            for container in containers:
                try:
                    # Determine volume path based on container type
                    if 'mongo' in container:
                        test_path = '/data/db'
                    elif 'opal' in container:
                        test_path = '/srv'
                    elif 'rock' in container:
                        test_path = '/srv'
                    else:
                        continue
                    
                    # Test read permissions first (less invasive)
                    read_test = subprocess.run(['docker', 'exec', container, 'ls', '-la', test_path], 
                                             capture_output=True, text=True, timeout=5)
                    
                    if read_test.returncode == 0:
                        console.print(f"[green]✓ {container}: Volume read permissions OK[/green]")
                        
                        # Test write permissions with a more robust approach
                        # Create a unique test file name
                        test_file = f'volume_test_{int(time.time() * 1000000)}'
                        
                        # Try to write and immediately remove test file
                        write_test = subprocess.run(['docker', 'exec', container, 'sh', '-c', f'touch {test_path}/{test_file} && rm {test_path}/{test_file}'], 
                                                  capture_output=True, text=True, timeout=5)
                        
                        if write_test.returncode == 0:
                            console.print(f"[green]✓ {container}: Volume write permissions OK[/green]")
                        else:
                            # Before reporting as an issue, check if the service is actually working
                            # by testing if existing files can be read
                            existing_files = subprocess.run(['docker', 'exec', container, 'ls', test_path], 
                                                           capture_output=True, text=True, timeout=5)
                            
                            if existing_files.returncode == 0 and existing_files.stdout.strip():
                                console.print(f"[yellow]⚠ {container}: Write test failed but volume contains data (likely functional)[/yellow]")
                                # Don't report this as an issue if there's existing data
                            else:
                                console.print(f"[red]✗ {container}: Cannot write to volume[/red]")
                                self.issues.append({
                                    'category': 'volumes',
                                    'severity': 'high',
                                    'title': f'Volume write permission issue ({container})',
                                    'description': f'Container {container} cannot write to its volume at {test_path}',
                                    'solution': 'Check Docker Desktop file sharing settings and volume permissions'
                                })
                    else:
                        console.print(f"[red]✗ {container}: Cannot read from volume[/red]")
                        self.issues.append({
                            'category': 'volumes',
                            'severity': 'high',
                            'title': f'Volume read permission issue ({container})',
                            'description': f'Container {container} cannot read from its volume at {test_path}',
                            'solution': 'Check Docker Desktop file sharing settings and volume permissions'
                        })
                        
                except Exception as e:
                    console.print(f"[yellow]⚠ Cannot check permissions for {container}: {str(e)}[/yellow]")
                    
        except Exception as e:
            console.print(f"[yellow]⚠ Cannot check macOS volume permissions: {str(e)}[/yellow]")

    def _check_volume_persistence(self):
        """Check that volumes persist data correctly."""
        try:
            console.print("[cyan]Checking volume persistence...[/cyan]")
            
            # Check volume age and usage
            volumes_result = subprocess.run(['docker', 'volume', 'ls', '--format', '{{.Name}}'], 
                                          capture_output=True, text=True, check=True, timeout=10)
            
            opal_volumes = [vol.strip() for vol in volumes_result.stdout.split('\n') 
                          if vol.strip() and self.stack_name in vol.strip()]
            
            for volume in opal_volumes:
                # Check when volume was created
                inspect_result = subprocess.run(['docker', 'volume', 'inspect', volume], 
                                              capture_output=True, text=True, check=True, timeout=10)
                
                if inspect_result.returncode == 0:
                    volume_info = json.loads(inspect_result.stdout)[0]
                    created_at = volume_info.get('CreatedAt', 'unknown')
                    
                    if created_at != 'unknown':
                        console.print(f"[green]✓ Volume {volume}: created at {created_at}[/green]")
                    
                    # Check if volume is being used by any containers
                    containers_result = subprocess.run(['docker', 'ps', '-a', '--filter', f'volume={volume}', '--format', '{{.Names}}'], 
                                                     capture_output=True, text=True, timeout=10)
                    
                    if containers_result.returncode == 0:
                        using_containers = [name.strip() for name in containers_result.stdout.split('\n') if name.strip()]
                        
                        if using_containers:
                            console.print(f"[green]✓ Volume {volume}: used by {len(using_containers)} container(s)[/green]")
                        else:
                            console.print(f"[yellow]⚠ Volume {volume}: not used by any containers[/yellow]")
                            self.issues.append({
                                'category': 'volumes',
                                'severity': 'medium',
                                'title': f'Unused volume detected',
                                'description': f'Volume {volume} is not being used by any containers',
                                'solution': 'Check if this volume is needed or can be removed with: docker volume rm'
                            })
                    
                    # Test data persistence by checking for expected files
                    self._check_volume_data_integrity(volume)
                    
        except Exception as e:
            console.print(f"[yellow]⚠ Cannot check volume persistence: {str(e)}[/yellow]")

    def _check_volume_data_integrity(self, volume_name: str):
        """Check if volume contains expected data for its service."""
        try:
            # Determine service type from volume name
            service_type = None
            if 'mongo' in volume_name:
                service_type = 'mongo'
            elif 'rock' in volume_name:
                service_type = 'rock'
            elif 'opal' in volume_name and 'data' in volume_name:
                service_type = 'opal'
            
            if not service_type:
                return
                
            # Find corresponding container
            containers_result = subprocess.run(['docker', 'ps', '--filter', f'name={self.stack_name}-{service_type}', '--format', '{{.Names}}'], 
                                             capture_output=True, text=True, check=True, timeout=10)
            
            containers = [name.strip() for name in containers_result.stdout.split('\n') if name.strip()]
            
            if not containers:
                return
                
            container = containers[0]
            
            # Check for expected files/directories
            if service_type == 'mongo':
                # Check for MongoDB data files
                check_result = subprocess.run(['docker', 'exec', container, 'ls', '-la', '/data/db'], 
                                            capture_output=True, text=True, timeout=10)
                if check_result.returncode == 0 and check_result.stdout.strip():
                    if 'collection' in check_result.stdout or 'WiredTiger' in check_result.stdout:
                        console.print(f"[green]✓ Volume {volume_name}: contains MongoDB data[/green]")
                    else:
                        console.print(f"[yellow]⚠ Volume {volume_name}: MongoDB data may be missing[/yellow]")
                        
            elif service_type == 'opal':
                # Check for OPAL data files
                check_result = subprocess.run(['docker', 'exec', container, 'ls', '-la', '/srv'], 
                                            capture_output=True, text=True, timeout=10)
                if check_result.returncode == 0 and check_result.stdout.strip():
                    if 'conf' in check_result.stdout or 'data' in check_result.stdout:
                        console.print(f"[green]✓ Volume {volume_name}: contains OPAL data[/green]")
                    else:
                        console.print(f"[yellow]⚠ Volume {volume_name}: OPAL data may be missing[/yellow]")
                        
            elif service_type == 'rock':
                # Check for Rock data files
                check_result = subprocess.run(['docker', 'exec', container, 'ls', '-la', '/srv'], 
                                            capture_output=True, text=True, timeout=10)
                if check_result.returncode == 0 and check_result.stdout.strip():
                    console.print(f"[green]✓ Volume {volume_name}: contains Rock data[/green]")
                    
        except Exception as e:
            console.print(f"[yellow]⚠ Cannot check data integrity for {volume_name}: {str(e)}[/yellow]")

    def check_aws_volume_issues(self):
        """Check AWS-specific volume configuration issues."""
        console.print("\n[bold cyan]☁️ Checking AWS Volume Configuration[/bold cyan]")
        
        # Skip if not on AWS
        if 'aws_instance_id' not in self.environment_info:
            console.print("[green]✓ Not running on AWS[/green]")
            return
            
        console.print(f"[yellow]⚠ Running on AWS instance: {self.environment_info['aws_instance_id']}[/yellow]")
        
        # Check EBS volume configuration
        self._check_aws_ebs_volumes()
        
        # Check instance storage
        self._check_aws_instance_storage()
        
        # Check volume performance
        self._check_aws_volume_performance()
        
        # Check backup configuration
        self._check_aws_backup_config()
        
        # Provide AWS-specific volume guidance
        self._show_aws_volume_guidance()

    def _check_aws_ebs_volumes(self):
        """Check EBS volume configuration."""
        try:
            # Check mounted volumes
            mount_result = subprocess.run(['mount'], capture_output=True, text=True, check=True, timeout=5)
            
            ebs_mounts = []
            for line in mount_result.stdout.split('\n'):
                if '/dev/nvme' in line or '/dev/xvd' in line:
                    ebs_mounts.append(line.strip())
            
            if ebs_mounts:
                console.print(f"[green]✓ Found {len(ebs_mounts)} EBS volumes mounted[/green]")
                for mount in ebs_mounts:
                    console.print(f"  {mount}")
                    
                # Check if Docker is using EBS storage
                docker_root = self.environment_info.get('docker_info', {}).get('DockerRootDir', '/var/lib/docker')
                df_result = subprocess.run(['df', docker_root], capture_output=True, text=True, check=True, timeout=5)
                
                if df_result.returncode == 0:
                    docker_device = df_result.stdout.split('\n')[1].split()[0]
                    if '/dev/nvme' in docker_device or '/dev/xvd' in docker_device:
                        console.print(f"[green]✓ Docker is using EBS storage: {docker_device}[/green]")
                        
                        # Check EBS volume type and performance
                        self._check_ebs_volume_type(docker_device)
                    else:
                        console.print(f"[yellow]⚠ Docker may not be using EBS storage: {docker_device}[/yellow]")
                        self.issues.append({
                            'category': 'aws_volumes',
                            'severity': 'medium',
                            'title': 'Docker not using EBS storage',
                            'description': f'Docker root directory is on {docker_device}, not EBS',
                            'solution': 'Consider moving Docker to EBS volume for better performance and persistence'
                        })
            else:
                console.print("[yellow]⚠ No EBS volumes detected[/yellow]")
                self.issues.append({
                    'category': 'aws_volumes',
                    'severity': 'medium',
                    'title': 'No EBS volumes detected',
                    'description': 'No EBS volumes found, data may not persist across instance restarts',
                    'solution': 'Attach EBS volumes for persistent storage'
                })
                
        except Exception as e:
            console.print(f"[yellow]⚠ Cannot check EBS volumes: {str(e)}[/yellow]")

    def _check_ebs_volume_type(self, device: str):
        """Check EBS volume type and performance characteristics."""
        try:
            # Try to get volume information from AWS metadata
            # This is a simplified check - in practice, you'd use AWS CLI or API
            console.print(f"[cyan]Checking EBS volume type for {device}[/cyan]")
            
            # Check I/O performance
            io_result = subprocess.run(['iostat', '-x', '1', '1'], capture_output=True, text=True, timeout=10)
            
            if io_result.returncode == 0:
                # Look for the device in iostat output
                lines = io_result.stdout.split('\n')
                for line in lines:
                    if device.split('/')[-1] in line:
                        parts = line.split()
                        if len(parts) >= 10:
                            util = parts[9]  # %util column
                            if util.replace('.', '').isdigit():
                                util_percent = float(util)
                                if util_percent > 80:
                                    self.issues.append({
                                        'category': 'aws_volumes',
                                        'severity': 'high',
                                        'title': 'High EBS volume utilization',
                                        'description': f'EBS volume {device} has {util_percent}% utilization',
                                        'solution': 'Consider upgrading to higher performance EBS volume (gp3, io1, io2)'
                                    })
                        break
            
            # Check for gp2 vs gp3 recommendations
            self.issues.append({
                'category': 'aws_volumes',
                'severity': 'low',
                'title': 'EBS volume type optimization',
                'description': 'Consider upgrading to gp3 volumes for better price/performance',
                'solution': 'AWS Console → EC2 → Volumes → Modify volume type to gp3'
            })
            
        except Exception as e:
            console.print(f"[yellow]⚠ Cannot check EBS volume performance: {str(e)}[/yellow]")

    def _check_aws_instance_storage(self):
        """Check AWS instance storage configuration."""
        try:
            # Check for instance store volumes
            lsblk_result = subprocess.run(['lsblk'], capture_output=True, text=True, check=True, timeout=5)
            
            if 'nvme' in lsblk_result.stdout:
                console.print("[green]✓ Instance has NVMe storage[/green]")
                
                # Check if instance store is being used
                if 'instance-store' in lsblk_result.stdout or any('ephemeral' in line for line in lsblk_result.stdout.split('\n')):
                    console.print("[yellow]⚠ Instance store detected[/yellow]")
                    self.issues.append({
                        'category': 'aws_volumes',
                        'severity': 'high',
                        'title': 'Instance store usage warning',
                        'description': 'Instance store provides temporary storage that is lost on instance stop/termination',
                        'solution': 'Use EBS volumes for persistent OPAL data storage'
                    })
                    
            # Check instance type for storage recommendations
            instance_type = self.environment_info.get('aws_instance_type', 'unknown')
            if instance_type != 'unknown':
                console.print(f"[green]✓ Instance type: {instance_type}[/green]")
                
                # Provide instance-specific storage recommendations
                if instance_type.startswith('t'):
                    self.issues.append({
                        'category': 'aws_volumes',
                        'severity': 'medium',
                        'title': 'Burstable instance storage consideration',
                        'description': f'T-series instances ({instance_type}) have burstable I/O performance',
                        'solution': 'Monitor I/O credits and consider M-series instances for consistent performance'
                    })
                elif instance_type.startswith('m5'):
                    console.print("[green]✓ Good instance type for general purpose workloads[/green]")
                    
        except Exception as e:
            console.print(f"[yellow]⚠ Cannot check instance storage: {str(e)}[/yellow]")

    def _check_aws_volume_performance(self):
        """Check AWS volume performance issues."""
        try:
            console.print("[cyan]Checking volume performance...[/cyan]")
            
            # Check for common performance issues
            # 1. Check if volumes are attached to the correct instance
            # 2. Check IOPS and throughput
            # 3. Check for bottlenecks
            
            # Simple disk performance test
            docker_root = self.environment_info.get('docker_info', {}).get('DockerRootDir', '/var/lib/docker')
            
            # Check current I/O wait
            iostat_result = subprocess.run(['iostat', '-x', '1', '1'], capture_output=True, text=True, timeout=10)
            
            if iostat_result.returncode == 0:
                # Look for high I/O wait
                lines = iostat_result.stdout.split('\n')
                for line in lines:
                    if 'avg-cpu' in line:
                        continue
                    if line.strip() and not line.startswith('Device'):
                        parts = line.split()
                        if len(parts) >= 4:
                            try:
                                iowait = float(parts[3])  # %iowait
                                if iowait > 20:
                                    self.issues.append({
                                        'category': 'aws_volumes',
                                        'severity': 'high',
                                        'title': 'High I/O wait detected',
                                        'description': f'I/O wait is {iowait}%, indicating storage bottleneck',
                                        'solution': 'Consider upgrading EBS volume type or size for better IOPS'
                                    })
                            except ValueError:
                                pass
                        break
            
            # Check disk queue depth
            self._check_disk_queue_depth()
            
        except Exception as e:
            console.print(f"[yellow]⚠ Cannot check volume performance: {str(e)}[/yellow]")

    def _check_disk_queue_depth(self):
        """Check disk queue depth for performance optimization."""
        try:
            # Check current queue depth settings
            sys_block_path = '/sys/block'
            if os.path.exists(sys_block_path):
                devices = [d for d in os.listdir(sys_block_path) if d.startswith('nvme') or d.startswith('xvd')]
                
                for device in devices:
                    queue_path = f'{sys_block_path}/{device}/queue/nr_requests'
                    if os.path.exists(queue_path):
                        with open(queue_path, 'r') as f:
                            queue_depth = int(f.read().strip())
                            
                        if queue_depth < 32:
                            self.issues.append({
                                'category': 'aws_volumes',
                                'severity': 'medium',
                                'title': 'Low disk queue depth',
                                'description': f'Device {device} has queue depth {queue_depth}',
                                'solution': f'Increase queue depth: echo 32 > /sys/block/{device}/queue/nr_requests'
                            })
                        else:
                            console.print(f"[green]✓ Device {device}: queue depth {queue_depth}[/green]")
                            
        except Exception as e:
            console.print(f"[yellow]⚠ Cannot check disk queue depth: {str(e)}[/yellow]")

    def _check_aws_backup_config(self):
        """Check AWS backup configuration for volumes."""
        console.print("[cyan]Checking backup configuration...[/cyan]")
        
        # This is informational - we can't directly check AWS Backup settings
        # but we can provide guidance
        self.issues.append({
            'category': 'aws_volumes',
            'severity': 'low',
            'title': 'AWS Backup configuration',
            'description': 'Ensure EBS volumes are included in AWS Backup plans',
            'solution': 'AWS Console → AWS Backup → Create backup plan for EBS volumes'
        })
        
        # Check for snapshot tags
        self.issues.append({
            'category': 'aws_volumes',
            'severity': 'low',
            'title': 'EBS snapshot management',
            'description': 'Configure automated EBS snapshots for data protection',
            'solution': 'AWS Console → EC2 → Snapshots → Create snapshot schedule'
        })

    def _show_aws_volume_guidance(self):
        """Show comprehensive AWS volume guidance."""
        console.print("\n[bold cyan]💡 AWS Volume Best Practices[/bold cyan]")
        
        guidance = [
            "1. **EBS Volume Types:**",
            "   • Use gp3 for general purpose (better price/performance than gp2)",
            "   • Use io1/io2 for high-performance databases",
            "   • Use st1 for throughput-intensive workloads",
            "",
            "2. **Volume Size and Performance:**",
            "   • Minimum 100GB for gp3 to get full 3,000 IOPS",
            "   • Size affects both storage and performance",
            "   • Monitor CloudWatch metrics for optimization",
            "",
            "3. **Mounting and Persistence:**",
            "   • Mount EBS volumes at instance launch",
            "   • Use consistent device names in /etc/fstab",
            "   • Enable EBS optimization on instance",
            "",
            "4. **Security and Encryption:**",
            "   • Enable EBS encryption at rest",
            "   • Use KMS keys for encryption",
            "   • Apply appropriate IAM permissions",
            "",
            "5. **Backup and Recovery:**",
            "   • Schedule regular EBS snapshots",
            "   • Use AWS Backup for automated backups",
            "   • Test restore procedures regularly",
            "",
            "6. **Monitoring and Optimization:**",
            "   • Monitor VolumeReadOps/VolumeWriteOps",
            "   • Watch for IOPS credit balance (gp2)",
            "   • Use CloudWatch Insights for analysis"
        ]
        
        for line in guidance:
            console.print(line)

    def check_system_resources(self):
        """Check system resources that might affect performance."""
        console.print("\n[bold cyan]💾 Checking System Resources[/bold cyan]")
        
        is_macos = self.environment_info.get('macos', False)
        
        # macOS-specific resource checks
        if is_macos:
            self._check_macos_resources()
            return
            
        try:
            # Check memory
            with open('/proc/meminfo', 'r') as f:
                mem_info = f.read()
                
            mem_total = int(re.search(r'MemTotal:\s+(\d+) kB', mem_info).group(1)) * 1024
            mem_available = int(re.search(r'MemAvailable:\s+(\d+) kB', mem_info).group(1)) * 1024
            
            mem_total_gb = mem_total / (1024**3)
            mem_available_gb = mem_available / (1024**3)
            
            console.print(f"[green]✓ Memory: {mem_available_gb:.1f}GB available / {mem_total_gb:.1f}GB total[/green]")
            
            if mem_available_gb < 2:
                self.issues.append({
                    'category': 'resources',
                    'severity': 'medium',
                    'title': 'Low memory available',
                    'description': f'Only {mem_available_gb:.1f}GB memory available',
                    'solution': 'Consider increasing system memory or closing other applications'
                })
                
            # Check disk space
            disk_result = subprocess.run(['df', '-h', '/'], capture_output=True, text=True, check=True)
            disk_lines = disk_result.stdout.split('\n')[1:]  # Skip header
            if disk_lines and disk_lines[0].strip():
                disk_info = disk_lines[0].split()
                if len(disk_info) >= 5:
                    disk_used_percent = disk_info[4].rstrip('%')
                    console.print(f"[green]✓ Disk space: {disk_used_percent}% used[/green]")
                    
                    if int(disk_used_percent) > 90:
                        self.issues.append({
                            'category': 'resources',
                            'severity': 'high',
                            'title': 'Low disk space',
                            'description': f'Disk is {disk_used_percent}% full',
                            'solution': 'Free up disk space or clean Docker images: docker system prune'
                        })
                        
        except Exception as e:
            console.print(f"[yellow]⚠ Cannot check system resources: {str(e)}[/yellow]")

    def _check_macos_resources(self):
        """Check system resources on macOS."""
        try:
            # Check memory usage
            vm_stat = subprocess.run(['vm_stat'], capture_output=True, text=True, check=True, timeout=5)
            
            if vm_stat.returncode == 0:
                # Parse vm_stat output for memory info
                lines = vm_stat.stdout.split('\n')
                for line in lines:
                    if 'Pages free:' in line:
                        free_pages = int(line.split(':')[1].strip().rstrip('.'))
                        free_mb = (free_pages * 4096) / (1024 * 1024)  # 4KB pages
                        console.print(f"[green]✓ Memory: {free_mb:.0f}MB free pages[/green]")
                        break
                        
            # Check memory pressure
            memory_pressure = subprocess.run(['memory_pressure'], capture_output=True, text=True, timeout=5)
            
            if memory_pressure.returncode == 0:
                if 'normal' in memory_pressure.stdout.lower():
                    console.print("[green]✓ Memory pressure: normal[/green]")
                elif 'warn' in memory_pressure.stdout.lower():
                    console.print("[yellow]⚠ Memory pressure: warning[/yellow]")
                    self.issues.append({
                        'category': 'resources',
                        'severity': 'medium',
                        'title': 'Memory pressure warning (macOS)',
                        'description': 'System is under memory pressure',
                        'solution': 'Close applications or restart Docker Desktop'
                    })
                elif 'critical' in memory_pressure.stdout.lower():
                    console.print("[red]✗ Memory pressure: critical[/red]")
                    self.issues.append({
                        'category': 'resources',
                        'severity': 'high',
                        'title': 'Critical memory pressure (macOS)',
                        'description': 'System is under critical memory pressure',
                        'solution': 'Restart system or increase available memory'
                    })
            
            # Check disk space
            df_result = subprocess.run(['df', '-h', '/'], capture_output=True, text=True, check=True, timeout=5)
            
            if df_result.returncode == 0:
                lines = df_result.stdout.split('\n')
                if len(lines) > 1:
                    parts = lines[1].split()
                    if len(parts) >= 5:
                        used_percent = parts[4].rstrip('%')
                        total_space = parts[1]
                        available_space = parts[3]
                        
                        console.print(f"[green]✓ Disk space: {used_percent}% used ({available_space} available of {total_space})[/green]")
                        
                        if int(used_percent) > 90:
                            self.issues.append({
                                'category': 'resources',
                                'severity': 'high',
                                'title': 'Low disk space (macOS)',
                                'description': f'System disk is {used_percent}% full',
                                'solution': 'Free up disk space or clean Docker: docker system prune'
                            })
                        elif int(used_percent) > 80:
                            self.issues.append({
                                'category': 'resources',
                                'severity': 'medium',
                                'title': 'Moderate disk usage (macOS)',
                                'description': f'System disk is {used_percent}% full',
                                'solution': 'Monitor disk space and consider cleanup'
                            })
            
            # Check Docker Desktop resource allocation
            docker_info = self.environment_info.get('docker_info', {})
            if docker_info:
                total_memory = docker_info.get('MemTotal', 0)
                if total_memory > 0:
                    memory_gb = total_memory / (1024**3)
                    console.print(f"[green]✓ Docker Desktop allocated memory: {memory_gb:.1f}GB[/green]")
                    
                    if memory_gb < 4:
                        self.issues.append({
                            'category': 'resources',
                            'severity': 'medium',
                            'title': 'Low Docker memory allocation (macOS)',
                            'description': f'Docker Desktop has only {memory_gb:.1f}GB allocated',
                            'solution': 'Increase Docker Desktop memory allocation in preferences'
                        })
                        
                cpus = docker_info.get('NCPU', 0)
                if cpus > 0:
                    console.print(f"[green]✓ Docker Desktop allocated CPUs: {cpus}[/green]")
                    
                    if cpus < 2:
                        self.issues.append({
                            'category': 'resources',
                            'severity': 'medium',
                            'title': 'Low Docker CPU allocation (macOS)',
                            'description': f'Docker Desktop has only {cpus} CPU(s) allocated',
                            'solution': 'Increase Docker Desktop CPU allocation in preferences'
                        })
            
        except Exception as e:
            console.print(f"[yellow]⚠ Cannot check macOS system resources: {str(e)}[/yellow]")
            
    def generate_report(self):
        """Generate a comprehensive diagnostic report."""
        console.print("\n[bold green]📋 Diagnostic Report[/bold green]")
        
        if not self.issues:
            console.print("[green]🎉 No issues detected! Your OPAL setup appears to be healthy.[/green]")
            return
            
        # Group issues by category
        issues_by_category = {}
        for issue in self.issues:
            category = issue['category']
            if category not in issues_by_category:
                issues_by_category[category] = []
            issues_by_category[category].append(issue)
            
        # Display issues by category
        for category, issues in issues_by_category.items():
            console.print(f"\n[bold yellow]{category.upper()} Issues:[/bold yellow]")
            
            for i, issue in enumerate(issues, 1):
                severity_color = {
                    'critical': 'red',
                    'high': 'red',
                    'medium': 'yellow',
                    'low': 'cyan'
                }.get(issue['severity'], 'white')
                
                console.print(f"  {i}. [{severity_color}]{issue['title']}[/{severity_color}]")
                console.print(f"     {issue['description']}")
                console.print(f"     [dim]Solution: {issue['solution']}[/dim]")
                
    def offer_automated_fixes(self):
        """Offer to apply automated fixes for detected issues."""
        if not self.fixes:
            return
            
        console.print("\n[bold cyan]🔧 Automated Fixes Available[/bold cyan]")
        
        for fix in self.fixes:
            console.print(f"\n[yellow]Fix: {fix['name']}[/yellow]")
            console.print(f"Description: {fix['description']}")
            console.print("Commands to run:")
            for cmd in fix['commands']:
                console.print(f"  [dim]{cmd}[/dim]")
                
            if fix.get('automatic', True):
                if Confirm.ask(f"Would you like to apply this fix?"):
                    console.print("[cyan]Applying fix...[/cyan]")
                    try:
                        for cmd in fix['commands']:
                            console.print(f"Running: {cmd}")
                            subprocess.run(cmd, shell=True, check=True)
                        console.print("[green]✓ Fix applied successfully[/green]")
                    except subprocess.CalledProcessError as e:
                        console.print(f"[red]✗ Fix failed: {e}[/red]")
            else:
                console.print("[yellow]⚠ This fix requires manual intervention[/yellow]")
                
    def run_full_diagnostic(self):
        """Run the complete diagnostic suite."""
        console.print(Panel.fit(
            "[bold blue]OPAL Network Diagnostic Tool[/bold blue]\n" +
            "This tool will comprehensively test your OPAL installation\n" +
            "and identify networking issues in various environments.",
            title="🔍 Network Diagnostics"
        ))
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True
        ) as progress:
            
            # Collect environment info
            task = progress.add_task("Collecting environment information...", total=None)
            self.collect_environment_info()
            progress.remove_task(task)
            
            # Check if running on macOS
            if self.environment_info.get('macos'):
                console.print("\n[bold yellow]🍎 macOS Development Environment Detected[/bold yellow]")
                console.print("This diagnostic tool is designed for both Linux production and macOS development.")
                console.print("For macOS development, comprehensive Docker, volume, and connectivity testing will be performed.")
                console.print("\n[dim]💡 To test production issues, run this tool on your Linux server[/dim]")
                
                # Run comprehensive checks for macOS
                macos_checks = [
                    ("Testing Docker connectivity", self.check_docker_connectivity),
                    ("Checking container status", self.check_container_status),
                    ("Testing container connectivity", self.check_container_connectivity),
                    ("Checking volume configuration", self.check_volume_issues),
                    ("Checking system resources", self.check_system_resources),
                ]
                
                for description, check_func in macos_checks:
                    task = progress.add_task(description, total=None)
                    try:
                        check_func()
                    except Exception as e:
                        console.print(f"[red]Error in {description}: {str(e)}[/red]")
                    progress.remove_task(task)
                    
                # Generate final report
                self.generate_report()
                return
            
            # Display environment summary
            self.display_environment_summary()
            
            # Check if containers are running before testing connectivity
            task = progress.add_task("Checking if containers are running...", total=None)
            containers_running = self._check_containers_running()
            progress.remove_task(task)
            
            if not containers_running:
                console.print("\n[bold yellow]⚠️  No containers are currently running[/bold yellow]")
                console.print("To test connectivity, containers need to be started first.")
                
                from rich.prompt import Confirm
                if Confirm.ask("Would you like to start the containers now?", default=True):
                    console.print("[cyan]Starting containers...[/cyan]")
                    try:
                        if run_docker_compose(["up", "-d"]):
                            console.print("[green]✓ Containers started successfully[/green]")
                            containers_running = True
                        else:
                            console.print("[red]✗ Failed to start containers[/red]")
                    except Exception as e:
                        console.print(f"[red]✗ Error starting containers: {e}[/red]")
                else:
                    console.print("Skipping container connectivity tests...")
                    console.print("Run: [bold cyan]./easy-opal up[/bold cyan] to start containers")
                
            # Run all diagnostic checks
            checks = [
                ("Testing Docker connectivity", self.check_docker_connectivity),
                ("Checking container status", self.check_container_status),
                ("Testing container connectivity", self.check_container_connectivity),
                ("Checking volume configuration", self.check_volume_issues),
                ("Checking SELinux configuration", self.check_selinux_issues),
                ("Checking firewall configuration", self.check_firewall_issues),
                ("Checking AWS configuration", self.check_aws_issues),
                ("Checking AWS volume configuration", self.check_aws_volume_issues),
                ("Checking Docker network configuration", self.check_docker_network_issues),
                ("Checking DNS resolution", self.check_dns_issues),
                ("Checking system resources", self.check_system_resources)
            ]
            
            for description, check_func in checks:
                task = progress.add_task(description, total=None)
                try:
                    check_func()
                except Exception as e:
                    console.print(f"[red]Error in {description}: {str(e)}[/red]")
                progress.remove_task(task)
                
        # Generate final report
        self.generate_report()
        
        # Offer automated fixes
        self.offer_automated_fixes()
        
        # AWS-specific guidance
        if 'aws_instance_id' in self.environment_info:
            self.show_aws_guidance()
        
        # SELinux-specific guidance
        if self.environment_info.get('selinux') == 'Enforcing':
            self.show_selinux_guidance()
            
    def _check_containers_running(self) -> bool:
        """Check if any containers are currently running."""
        try:
            result = subprocess.run(['docker', 'ps', '--filter', f'name={self.stack_name}', '--quiet'], 
                                  capture_output=True, text=True, check=True, timeout=10)
            return bool(result.stdout.strip())
        except Exception:
            return False
            
    def show_aws_guidance(self):
        """Show detailed AWS-specific guidance."""
        console.print("\n[bold cyan]☁️ AWS-Specific Guidance[/bold cyan]")
        
        aws_guidance = Panel.fit(
            "[bold]AWS Security Groups Configuration:[/bold]\n\n" +
            "1. Go to AWS Console → EC2 → Security Groups\n" +
            "2. Select your instance's security group\n" +
            "3. Edit Inbound Rules:\n" +
            f"   • Add rule: HTTPS (443) from your IP range\n" +
            f"   • Add rule: Custom TCP ({self.config.get('opal_external_port', 443)}) from your IP range\n" +
            f"   • For development: HTTP (80) from your IP range\n\n" +
            "[bold]Network ACLs (NACLs):[/bold]\n" +
            "1. Go to VPC → Network ACLs\n" +
            "2. Check that your subnet's NACL allows:\n" +
            "   • Inbound: HTTP (80), HTTPS (443), Custom ports\n" +
            "   • Outbound: All traffic or specific ports\n\n" +
            "[bold]VPC Configuration:[/bold]\n" +
            "• Ensure your instance is in a public subnet (if accessing from internet)\n" +
            "• Check route tables point to Internet Gateway\n" +
            "• Verify DNS resolution is enabled in VPC settings",
            title="🌐 AWS Network Configuration"
        )
        console.print(aws_guidance)
        
    def show_selinux_guidance(self):
        """Show detailed SELinux-specific guidance."""
        console.print("\n[bold cyan]🛡️ SELinux-Specific Guidance[/bold cyan]")
        
        selinux_guidance = Panel.fit(
            "[bold]SELinux Configuration for Docker:[/bold]\n\n" +
            "[bold]Option 1: Configure SELinux properly (Recommended)[/bold]\n" +
            "Run these commands to allow Docker operations:\n" +
            "  sudo setsebool -P container_manage_cgroup on\n" +
            "  sudo setsebool -P container_connect_any on\n" +
            "  sudo setsebool -P container_use_cephfs on\n\n" +
            "[bold]Option 2: Set SELinux to Permissive (Development)[/bold]\n" +
            "For development environments:\n" +
            "  sudo setenforce 0\n" +
            "  # To make permanent: edit /etc/selinux/config\n\n" +
            "[bold]Option 3: Create custom SELinux policy[/bold]\n" +
            "For production environments, create a custom policy:\n" +
            "  sudo ausearch -m avc -ts recent | audit2allow -M mydocker\n" +
            "  sudo semodule -i mydocker.pp\n\n" +
            "[bold]Troubleshooting:[/bold]\n" +
            "• Check denials: sudo ausearch -m avc -ts recent\n" +
            "• Monitor in real-time: sudo tail -f /var/log/audit/audit.log\n" +
            "• Test with SELinux disabled temporarily: sudo setenforce 0",
            title="🔒 SELinux Configuration"
        )
        console.print(selinux_guidance)


@click.command()
@click.option('--quick', is_flag=True, help='Run quick diagnostic (skip detailed checks)')
@click.option('--fix', is_flag=True, help='Automatically apply safe fixes')
@click.option('--json', 'output_json', is_flag=True, help='Output results in JSON format')
def diagnose(quick, fix, output_json):
    """Comprehensive network diagnostic tool for OPAL installations.
    
    This tool performs extensive testing of your OPAL setup, including:
    - Docker connectivity and container status
    - Inter-container communication
    - SELinux configuration issues
    - AWS-specific networking problems
    - Firewall and iptables configuration
    - DNS resolution problems
    - System resource constraints
    
    The tool provides detailed explanations of issues found and offers
    automated fixes where possible.
    """
    
    diagnostic = NetworkDiagnostic()
    
    if quick:
        console.print("[cyan]Running quick diagnostic...[/cyan]")
        diagnostic.collect_environment_info()
        
        # Check if running on macOS
        if diagnostic.environment_info.get('macos'):
            console.print("\n[bold yellow]🍎 macOS Development Environment Detected[/bold yellow]")
            console.print("Quick diagnostic will test Docker and container connectivity.")
            console.print("\n[dim]💡 For production troubleshooting, run this on your Linux server[/dim]")
        
        diagnostic.check_docker_connectivity()
        diagnostic.check_container_status()
        diagnostic.check_container_connectivity()
        diagnostic.generate_report()
    else:
        diagnostic.run_full_diagnostic()
        
    if output_json:
        # Output JSON report
        json_report = {
            'environment': diagnostic.environment_info,
            'issues': diagnostic.issues,
            'fixes': diagnostic.fixes
        }
        console.print(json.dumps(json_report, indent=2)) 