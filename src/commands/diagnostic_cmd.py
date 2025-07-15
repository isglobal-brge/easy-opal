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
        
        selinux_status = self.environment_info.get('selinux', 'Unknown')
        
        if selinux_status == 'Enforcing':
            console.print("[yellow]⚠ SELinux is in Enforcing mode[/yellow]")
            
            # Check for Docker-related SELinux denials
            try:
                audit_result = subprocess.run(['sudo', 'ausearch', '-m', 'avc', '-ts', 'recent'], 
                                            capture_output=True, text=True, timeout=10)
                
                if audit_result.returncode == 0 and 'docker' in audit_result.stdout.lower():
                    console.print("[red]✗ SELinux denials detected for Docker[/red]")
                    self.issues.append({
                        'category': 'selinux',
                        'severity': 'high',
                        'title': 'SELinux blocking Docker operations',
                        'description': 'SELinux is blocking Docker operations. This can prevent container connectivity.',
                        'solution': 'Set SELinux to permissive mode: sudo setsebool -P container_manage_cgroup on'
                    })
                    
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
            
    def check_system_resources(self):
        """Check system resources that might affect performance."""
        console.print("\n[bold cyan]💾 Checking System Resources[/bold cyan]")
        
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
                console.print("This diagnostic tool is designed for Linux production environments.")
                console.print("For macOS development, Docker and container connectivity will be tested.")
                console.print("\n[dim]💡 To test production issues, run this tool on your Linux server[/dim]")
                
                # Run basic checks including connectivity
                basic_checks = [
                    ("Testing Docker connectivity", self.check_docker_connectivity),
                    ("Checking container status", self.check_container_status),
                    ("Testing container connectivity", self.check_container_connectivity),
                ]
                
                for description, check_func in basic_checks:
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
                ("Checking SELinux configuration", self.check_selinux_issues),
                ("Checking firewall configuration", self.check_firewall_issues),
                ("Checking AWS configuration", self.check_aws_issues),
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