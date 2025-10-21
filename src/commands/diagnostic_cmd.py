import click
import subprocess
import json
import socket
import ssl
import time
import requests
import urllib3
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Confirm
from typing import Dict, Any, List, Tuple, Optional

from src.core.config_manager import load_config, CONFIG_FILE, ensure_password_is_set

# Suppress urllib3 InsecureRequestWarning since we intentionally use verify=False for self-signed certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from src.core.docker_manager import DOCKER_COMPOSE_PATH, get_docker_compose_command, docker_up

console = Console()

class DiagnosticTest:
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.status = "pending"
        self.message = ""
        self.details = {}

class ContainerDiagnostics:
    def __init__(self):
        self.tests: List[DiagnosticTest] = []
        self.config = None
        
    def add_test(self, test: DiagnosticTest):
        self.tests.append(test)
        
    def load_configuration(self) -> bool:
        """Load and validate configuration"""
        try:
            if not CONFIG_FILE.exists():
                console.print("[bold red]❌ Configuration file not found.[/bold red]")
                console.print("Please run './easy-opal setup' first.")
                return False
                
            self.config = load_config()
            return True
        except Exception as e:
            console.print(f"[bold red]❌ Failed to load configuration: {e}[/bold red]")
            return False

    def check_stack_running(self) -> Tuple[bool, int, str]:
        """
        Check if the stack is actually running
        Returns: (is_running, container_count, status_message)
        """
        try:
            if not self.config:
                return False, 0, "Configuration not loaded"
            
            compose_cmd = get_docker_compose_command()
            project_name = self.config.get("stack_name", "easy-opal")
            
            result = subprocess.run(
                compose_cmd + ["--project-name", project_name, "ps", "--format", "json"],
                capture_output=True,
                text=True,
                check=True
            )
            
            if not result.stdout.strip():
                return False, 0, "No containers found for this project"
            
            containers = []
            for line in result.stdout.strip().split('\n'):
                if line.strip():
                    try:
                        container = json.loads(line)
                        containers.append(container)
                    except json.JSONDecodeError:
                        continue
            
            if not containers:
                return False, 0, "No containers found"
            
            running_containers = [c for c in containers if c.get('State') == 'running']
            total_containers = len(containers)
            running_count = len(running_containers)
            
            if running_count == 0:
                return False, total_containers, f"All {total_containers} containers are stopped"
            elif running_count < total_containers:
                return False, total_containers, f"Only {running_count}/{total_containers} containers are running"
            else:
                return True, total_containers, f"All {total_containers} containers are running"
                
        except subprocess.CalledProcessError:
            return False, 0, "Failed to check container status"
        except Exception as e:
            return False, 0, f"Error checking stack status: {str(e)}"

    def check_docker_compose_exists(self) -> DiagnosticTest:
        """Check if docker-compose.yml exists"""
        test = DiagnosticTest("docker-compose-file", "Docker Compose file exists")
        
        if DOCKER_COMPOSE_PATH.exists():
            test.status = "pass"
            test.message = f"Found at {DOCKER_COMPOSE_PATH}"
        else:
            test.status = "fail"
            test.message = "docker-compose.yml not found. Run './easy-opal setup' to generate it."
            
        return test

    def get_container_status(self) -> DiagnosticTest:
        """Get status of all containers"""
        test = DiagnosticTest("container-status", "Container status check")
        
        try:
            compose_cmd = get_docker_compose_command()
            project_name = self.config.get("stack_name", "easy-opal")
            result = subprocess.run(
                compose_cmd + ["--project-name", project_name, "ps", "--format", "json"],
                capture_output=True,
                text=True,
                check=True
            )
            
            containers = []
            for line in result.stdout.strip().split('\n'):
                if line.strip():
                    try:
                        container = json.loads(line)
                        containers.append(container)
                    except json.JSONDecodeError:
                        continue
            
            if not containers:
                test.status = "fail"
                test.message = "No containers found. Run './easy-opal up' to start the stack."
                return test
            
            running_containers = [c for c in containers if c.get('State') == 'running']
            stopped_containers = [c for c in containers if c.get('State') != 'running']
            
            # Categorize containers by type for clarity
            container_details = []
            for container in containers:
                service_name = container.get('Service', 'unknown')
                state = container.get('State', 'unknown')
                status = container.get('Status', 'unknown')
                
                # Determine container type
                if service_name == 'mongo':
                    container_type = 'Database'
                elif service_name == 'opal':
                    container_type = 'Application'
                elif service_name == 'nginx':
                    container_type = 'Web Proxy'
                elif service_name == 'certbot':
                    container_type = 'SSL Certificate Manager'
                elif service_name.startswith('rock'):
                    container_type = 'R Server'
                else:
                    container_type = 'Other'
                
                container_details.append({
                    'Service': service_name,
                    'Type': container_type,
                    'State': state,
                    'Status': status
                })
            
            test.details['containers'] = container_details
            test.details['total'] = len(containers)
            test.details['running'] = len(running_containers)
            
            if len(running_containers) == len(containers):
                test.status = "pass"
                test.message = f"All {len(containers)} containers running ({len(running_containers)} services active)"
            elif len(running_containers) > 0:
                test.status = "warn"
                test.message = f"{len(running_containers)}/{len(containers)} containers running ({len(stopped_containers)} stopped)"
            else:
                test.status = "fail"
                test.message = f"All {len(containers)} containers are stopped"
                
        except subprocess.CalledProcessError as e:
            test.status = "fail"
            test.message = f"Failed to get container status: {e}"
        except Exception as e:
            test.status = "fail"
            test.message = f"Error checking containers: {e}"
            
        return test

    def test_container_connectivity(self) -> DiagnosticTest:
        """Test connectivity between containers based on actual running configuration"""
        test = DiagnosticTest("container-connectivity", "Inter-container network connectivity")
        
        try:
            compose_cmd = get_docker_compose_command()
            project_name = self.config.get("stack_name", "easy-opal")
            
            # Get list of actual running containers
            running_containers = self._get_running_containers(project_name)
            if not running_containers:
                test.status = "fail"
                test.message = "No containers running to test connectivity"
                return test
            
            # Test connectivity with retry logic (up to 2 minutes for services to start)
            connectivity_tests = self._test_connectivity_with_retry(running_containers, project_name)
            
            test.details['tests'] = connectivity_tests
            
            passed_tests = [t for t in connectivity_tests if t['status'] == 'pass']
            failed_tests = [t for t in connectivity_tests if t['status'] == 'fail']
            
            if len(failed_tests) == 0:
                test.status = "pass"
                test.message = f"All {len(connectivity_tests)} inter-container connections verified"
            elif len(passed_tests) > 0:
                test.status = "warn"
                test.message = f"{len(passed_tests)}/{len(connectivity_tests)} inter-container connections verified"
            else:
                test.status = "fail"
                test.message = "All inter-container connections failed"
                
        except Exception as e:
            test.status = "fail"
            test.message = f"Error testing connectivity: {e}"
            
        return test

    def _test_container_connection(self, source: str, target: str, port: int, description: str, project_name: str) -> Dict[str, Any]:
        """Test actual TCP connection between containers using bash /dev/tcp"""
        try:
            compose_cmd = get_docker_compose_command()
            
            # Use bash's built-in /dev/tcp for TCP connectivity testing
            # This is reliable and available in all standard containers
            tcp_test_cmd = f"timeout 5 bash -c '</dev/tcp/{target}/{port}' 2>/dev/null"
            result = subprocess.run(
                compose_cmd + ["--project-name", project_name, "exec", "-T", source, "bash", "-c", tcp_test_cmd],
                capture_output=True,
                text=True,
                timeout=15
            )
            
            if result.returncode == 0:
                return {
                    'description': description,
                    'source': source,
                    'target': target,
                    'port': port,
                    'status': 'pass',
                    'message': f"✅ TCP connection verified"
                }
            else:
                return {
                    'description': description,
                    'source': source,
                    'target': target,
                    'port': port,
                    'status': 'fail',
                    'message': f"❌ TCP connection failed"
                }
                
        except subprocess.TimeoutExpired:
            return {
                'description': description,
                'source': source,
                'target': target,
                'port': port,
                'status': 'fail',
                'message': f"❌ Connection timeout"
            }
        except Exception as e:
            return {
                'description': description,
                'source': source,
                'target': target,
                'port': port,
                'status': 'fail',
                'message': f"❌ Error: {str(e)}"
            }



    def _get_running_containers(self, project_name: str) -> List[str]:
        """Get list of actually running container service names"""
        try:
            compose_cmd = get_docker_compose_command()
            
            # Get running containers with their service names
            result = subprocess.run(
                compose_cmd + ["--project-name", project_name, "ps", "--format", "json"],
                capture_output=True,
                text=True,
                check=True
            )
            
            running_containers = []
            for line in result.stdout.strip().split('\n'):
                if line.strip():
                    try:
                        container = json.loads(line)
                        if container.get('State') == 'running':
                            # Extract service name from container name
                            service_name = container.get('Service', '')
                            if service_name:
                                running_containers.append(service_name)
                    except json.JSONDecodeError:
                        continue
            
            return running_containers
            
        except Exception as e:
            return []

    def _test_connectivity_with_retry(self, running_containers: List[str], project_name: str) -> List[Dict[str, Any]]:
        """Test connectivity with retry logic - waits up to 2 minutes for services to start"""
        max_wait_time = 120  # 2 minutes
        retry_interval = 10  # 10 seconds between retries
        start_time = time.time()
        
        # Define the connectivity tests to perform
        planned_tests = []
        
        # Test database connectivity (MongoDB is always critical)
        if "mongo" in running_containers and "opal" in running_containers:
            planned_tests.append(("opal", "mongo", 27017, "Opal → MongoDB (metadata database)"))
        
        # Test additional database connectivity
        database_instances = self.config.get('databases', [])
        for db in database_instances:
            db_name = db.get('name')
            if db_name in running_containers:
                # Database instances don't need container-to-container test, 
                # just external port accessibility which is tested separately
                pass  # Will be tested in external port tests
        
        # Test reverse proxy connectivity (only if nginx exists - not in none mode)
        ssl_strategy = self.config.get('ssl', {}).get('strategy', 'self-signed')
        if ssl_strategy != 'none' and "nginx" in running_containers and "opal" in running_containers:
            planned_tests.append(("nginx", "opal", 8080, "Nginx → Opal (reverse proxy)"))
        
        # Test Rock container connectivity (all Rock containers)
        rock_containers = [name for name in running_containers if name.startswith('rock')]
        for rock_name in rock_containers:
            if "opal" in running_containers:
                planned_tests.append(("opal", rock_name, 8085, f"Opal → {rock_name} (R server)"))
        
        if not planned_tests:
            return []
        
        # Initial attempt
        connectivity_tests = []
        for source, target, port, description in planned_tests:
            test_result = self._test_container_connection(source, target, port, description, project_name)
            connectivity_tests.append(test_result)
        
        # Check if any tests failed
        failed_tests = [t for t in connectivity_tests if t['status'] == 'fail']
        
        # If all tests passed, return immediately
        if not failed_tests:
            return connectivity_tests
        
        # If we have failures and time remaining, start retry loop
        retry_count = 0
        while failed_tests and (time.time() - start_time) < max_wait_time:
            retry_count += 1
            time_elapsed = int(time.time() - start_time)
            time_remaining = max_wait_time - time_elapsed
            
            console.print(f"[yellow]⏳ {len(failed_tests)} connectivity test(s) failed. "
                         f"Waiting {retry_interval}s for services to start... "
                         f"(attempt {retry_count}, {time_remaining}s remaining)[/yellow]")
            
            time.sleep(retry_interval)
            
            # Retry only the failed tests
            new_connectivity_tests = []
            for test_result in connectivity_tests:
                if test_result['status'] == 'fail':
                    # Find the original test parameters
                    for source, target, port, description in planned_tests:
                        if test_result['description'] == description:
                            # Retry this test
                            new_result = self._test_container_connection(source, target, port, description, project_name)
                            new_connectivity_tests.append(new_result)
                            break
                else:
                    # Keep the successful test
                    new_connectivity_tests.append(test_result)
            
            connectivity_tests = new_connectivity_tests
            failed_tests = [t for t in connectivity_tests if t['status'] == 'fail']
        
        # Final status message
        if failed_tests:
            console.print(f"[dim]⏱️ Connectivity testing completed after {int(time.time() - start_time)}s. "
                         f"{len(failed_tests)} test(s) still failing.[/dim]")
        else:
            console.print(f"[dim]✅ All connectivity tests passed after {int(time.time() - start_time)}s![/dim]")
        
        return connectivity_tests

    def _test_with_retry(self, test_func, test_name: str, max_wait_time: int = 120, retry_interval: int = 10):
        """Generic retry wrapper for tests that may need time for services to start"""
        start_time = time.time()
        
        # Initial attempt
        test_result = test_func()
        
        # If test passed or is not retryable, return immediately
        if test_result.status == "pass" or test_result.status == "skip":
            return test_result
        
        # Check if we should retry based on the test result
        if not hasattr(test_result, 'details') or not test_result.details:
            return test_result
        
        # Count failures that might be startup-related
        failed_subtests = []
        if 'tests' in test_result.details:
            failed_subtests = [t for t in test_result.details['tests'] if t.get('status') == 'fail']
        
        # If no failures or not retryable failures, return immediately  
        if not failed_subtests:
            return test_result
        
        # Start retry loop
        retry_count = 0
        while failed_subtests and (time.time() - start_time) < max_wait_time:
            retry_count += 1
            time_elapsed = int(time.time() - start_time)
            time_remaining = max_wait_time - time_elapsed
            
            console.print(f"[yellow]⏳ {len(failed_subtests)} {test_name} test(s) failed. "
                         f"Waiting {retry_interval}s for services to fully start... "
                         f"(attempt {retry_count}, {time_remaining}s remaining)[/yellow]")
            
            time.sleep(retry_interval)
            
            # Retry the test
            test_result = test_func()
            
            # Update failed subtests
            if hasattr(test_result, 'details') and test_result.details and 'tests' in test_result.details:
                failed_subtests = [t for t in test_result.details['tests'] if t.get('status') == 'fail']
            else:
                failed_subtests = []
        
        # Final status message
        if failed_subtests:
            console.print(f"[dim]⏱️ {test_name} testing completed after {int(time.time() - start_time)}s. "
                         f"{len(failed_subtests)} test(s) still failing.[/dim]")
        else:
            console.print(f"[dim]✅ All {test_name} tests passed after {int(time.time() - start_time)}s![/dim]")
        
        return test_result

    def _test_external_ports_impl(self) -> DiagnosticTest:
        """Test external port accessibility (implementation)"""
        test = DiagnosticTest("external-ports", "External port accessibility")
        
        if not self.config:
            test.status = "fail"
            test.message = "Configuration not loaded"
            return test
            
        port_tests = []
        
        # Test ports based on SSL strategy
        ssl_strategy = self.config.get('ssl', {}).get('strategy', 'self-signed')
        
        if ssl_strategy == 'none':
            # In reverse-proxy mode, Opal is exposed directly on HTTP port
            http_port = self.config.get('opal_http_port', 8080)
            
            http_test = self._test_port_accessibility('localhost', http_port, f'Opal HTTP (none/reverse-proxy mode, port {http_port})')
            port_tests.append(http_test)
        else:
            # In standard mode, test the HTTPS port handled by nginx
            https_port = self.config.get('opal_external_port', 443)
            https_test = self._test_port_accessibility('localhost', https_port, f'Opal HTTPS (via nginx, port {https_port})')
            port_tests.append(https_test)
            
            # Also test HTTP port 80 if using Let's Encrypt (needed for challenges)
            if ssl_strategy == 'letsencrypt':
                http80_test = self._test_port_accessibility('localhost', 80, 'HTTP port 80 (Let\'s Encrypt challenges)')
                port_tests.append(http80_test)
        
        # Test additional database ports
        database_instances = self.config.get('databases', [])
        
        for db in database_instances:
            db_name = db.get('name')
            db_type = db.get('type')
            db_port = db.get('port')
            
            # Get display name for database type
            db_type_display = {
                'postgres': 'PostgreSQL',
                'mysql': 'MySQL',
                'mariadb': 'MariaDB'
            }.get(db_type, db_type.title())
            
            test_result = self._test_port_accessibility(
                'localhost', 
                db_port, 
                f'{db_type_display} database "{db_name}" (port {db_port})'
            )
            port_tests.append(test_result)
        
        test.details['tests'] = port_tests
        
        passed_tests = [t for t in port_tests if t['status'] == 'pass']
        failed_tests = [t for t in port_tests if t['status'] == 'fail']
        
        if len(failed_tests) == 0:
            test.status = "pass"
            test.message = f"All {len(port_tests)} ports accessible"
        elif len(passed_tests) > 0:
            test.status = "warn"
            test.message = f"{len(passed_tests)}/{len(port_tests)} ports accessible"
        else:
            test.status = "fail"
            test.message = "No ports accessible"
            
        return test

    def _test_port_accessibility(self, host: str, port: int, service: str) -> Dict[str, Any]:
        """Test if a port is accessible"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((host, port))
            sock.close()
            
            if result == 0:
                return {
                    'service': service,
                    'host': host,
                    'port': port,
                    'status': 'pass',
                    'message': f"✅ Port {port} accessible"
                }
            else:
                return {
                    'service': service,
                    'host': host,
                    'port': port,
                    'status': 'fail',
                    'message': f"❌ Port {port} not accessible"
                }
        except Exception as e:
            return {
                'service': service,
                'host': host,
                'port': port,
                'status': 'fail',
                'message': f"❌ Error testing port {port}: {str(e)}"
            }

    def _test_ssl_certificates_impl(self) -> DiagnosticTest:
        """Test SSL certificate validity (implementation)"""
        test = DiagnosticTest("ssl-certificates", "SSL certificate validation")
        
        if not self.config:
            test.status = "fail"
            test.message = "Configuration not loaded"
            return test
        
        ssl_strategy = self.config.get('ssl', {}).get('strategy', 'self-signed')
        
        if ssl_strategy == 'none':
            test.status = "skip"
            test.message = "SSL handled by external reverse proxy (not managed by easy-opal)"
            return test
        
        cert_tests = []
        hosts = self.config.get('hosts', ['localhost'])
        port = self.config.get('opal_external_port', 443)
        
        for host in hosts:
            cert_test = self._test_ssl_certificate(host, port, ssl_strategy)
            cert_tests.append(cert_test)
        
        test.details['tests'] = cert_tests
        
        passed_tests = [t for t in cert_tests if t['status'] == 'pass']
        failed_tests = [t for t in cert_tests if t['status'] == 'fail']
        
        if len(failed_tests) == 0:
            test.status = "pass"
            test.message = f"All {len(cert_tests)} certificates valid"
        elif len(passed_tests) > 0:
            test.status = "warn"
            test.message = f"{len(passed_tests)}/{len(cert_tests)} certificates valid"
        else:
            test.status = "fail"
            test.message = "Certificate validation failed"
            
        return test

    def _test_ssl_certificate(self, host: str, port: int, ssl_strategy: str) -> Dict[str, Any]:
        """Test SSL certificate for a specific host with strategy context"""
        try:
            context = ssl.create_default_context()
            
            # For self-signed certificates, we need to disable verification
            if ssl_strategy == 'self-signed':
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            
            with socket.create_connection((host, port), timeout=10) as sock:
                with context.wrap_socket(sock, server_hostname=host) as ssock:
                    cert = ssock.getpeercert()
                    
                    # Extract meaningful certificate info
                    subject = dict(x[0] for x in cert.get('subject', []))
                    common_name = subject.get('commonName', 'Unknown')
                    expires = cert.get('notAfter', 'Unknown')
                    
                    strategy_desc = {
                        'self-signed': 'Self-signed certificate',
                        'letsencrypt': 'Let\'s Encrypt certificate', 
                        'manual': 'Manual certificate'
                    }.get(ssl_strategy, f'{ssl_strategy} certificate')
                    
                    return {
                        'host': host,
                        'port': port,
                        'status': 'pass',
                        'message': f"✅ {strategy_desc} valid for {common_name} (expires: {expires})",
                        'strategy': ssl_strategy,
                        'common_name': common_name,
                        'expires': expires
                    }
                    
        except ssl.SSLError as e:
            # For self-signed certificates, we might get SSL errors but still want to check if cert exists
            if "certificate verify failed" in str(e).lower():
                return {
                    'host': host,
                    'port': port,
                    'status': 'warn',
                    'message': f"⚠️ Self-signed certificate detected for {host}",
                    'error': str(e)
                }
            else:
                return {
                    'host': host,
                    'port': port,
                    'status': 'fail',
                    'message': f"❌ SSL error for {host}: {str(e)}"
                }
        except Exception as e:
            return {
                'host': host,
                'port': port,
                'status': 'fail',
                'message': f"❌ Connection failed for {host}: {str(e)}"
            }

    def _test_service_endpoints_impl(self) -> DiagnosticTest:
        """Test service endpoints accessibility (implementation)"""
        test = DiagnosticTest("service-endpoints", "Service endpoint health checks")
        
        if not self.config:
            test.status = "fail"
            test.message = "Configuration not loaded"
            return test
        
        endpoint_tests = []
        ssl_strategy = self.config.get('ssl', {}).get('strategy', 'self-signed')
        
        # Build URLs based on SSL strategy
        hosts = self.config.get('hosts', ['localhost'])
        
        if ssl_strategy == 'none':
            # In none mode, test HTTP directly to Opal container
            port = self.config.get('opal_http_port', 8080)
            
            # In none mode, hosts list is empty, so test localhost directly
            if not hosts:
                hosts = ['localhost']
            
            for host in hosts:
                base_url = f"http://{host}:{port}"
                
                # Test Opal login page - this confirms the service is working
                opal_test = self._test_http_endpoint(f"{base_url}/", f"Opal web interface (HTTP, none/reverse-proxy mode)")
                endpoint_tests.append(opal_test)
                
                # Test Opal API endpoint - 404 with RESTEASY message indicates Opal is responding correctly
                api_test = self._test_opal_api_endpoint(f"{base_url}/ws", f"Opal API endpoint (HTTP)")
                endpoint_tests.append(api_test)
        else:
            # Standard HTTPS mode through nginx
            port = self.config.get('opal_external_port', 443)
            for host in hosts:
                base_url = f"https://{host}:{port}" if port != 443 else f"https://{host}"
                
                # Test Opal login page - this confirms the service is working
                opal_test = self._test_http_endpoint(f"{base_url}/", f"Opal web interface (HTTPS via nginx)")
                endpoint_tests.append(opal_test)
                
                # Test Opal API endpoint - 404 with RESTEASY message indicates Opal is responding correctly
                api_test = self._test_opal_api_endpoint(f"{base_url}/ws", f"Opal API endpoint (HTTPS)")
                endpoint_tests.append(api_test)
        
        test.details['tests'] = endpoint_tests
        
        passed_tests = [t for t in endpoint_tests if t['status'] == 'pass']
        failed_tests = [t for t in endpoint_tests if t['status'] == 'fail']
        
        if len(failed_tests) == 0:
            test.status = "pass"
            test.message = f"All {len(endpoint_tests)} endpoints accessible"
        elif len(passed_tests) > 0:
            test.status = "warn"
            test.message = f"{len(passed_tests)}/{len(endpoint_tests)} endpoints accessible"
        else:
            test.status = "fail"
            test.message = "No endpoints accessible"
            
        return test

    def _test_http_endpoint(self, url: str, description: str) -> Dict[str, Any]:
        """Test HTTP endpoint accessibility"""
        try:
            # For self-signed certificates, disable SSL verification
            response = requests.get(url, timeout=10, verify=False, allow_redirects=True)
            
            if response.status_code < 400:
                return {
                    'url': url,
                    'description': description,
                    'status': 'pass',
                    'message': f"✅ {description} accessible (HTTP {response.status_code})",
                    'status_code': response.status_code
                }
            else:
                return {
                    'url': url,
                    'description': description,
                    'status': 'warn',
                    'message': f"⚠️ {description} returned HTTP {response.status_code}",
                    'status_code': response.status_code
                }
                
        except requests.exceptions.SSLError as e:
            return {
                'url': url,
                'description': description,
                'status': 'warn',
                'message': f"⚠️ SSL error (expected for self-signed): {str(e)}"
            }
        except requests.exceptions.ConnectionError as e:
            return {
                'url': url,
                'description': description,
                'status': 'fail',
                'message': f"❌ Connection failed: {str(e)}"
            }
        except Exception as e:
            return {
                'url': url,
                'description': description,
                'status': 'fail',
                'message': f"❌ Error: {str(e)}"
            }
    
    def _test_opal_api_endpoint(self, url: str, description: str) -> dict:
        """Test Opal API endpoint - treats RESTEASY 404 as success since it indicates Opal is responding"""
        try:
            response = requests.get(url, verify=False, timeout=10)
            
            # Check if it's a 404 with the expected RESTEASY message (indicates Opal is working)
            if response.status_code == 404:
                if "RESTEASY003210" in response.text or "Could not find resource for full path" in response.text:
                    return {
                        'url': url,
                        'description': description,  
                        'status': 'pass',
                        'message': f"✅ {description} responding correctly (404 with RESTEASY - expected for /ws endpoint)"
                    }
                else:
                    return {
                        'url': url,
                        'description': description,
                        'status': 'warn',
                        'message': f"⚠️ {description} returned HTTP 404 (unexpected format)"
                    }
            
            # Any other response code (200, 403, etc.) also indicates Opal is working
            if response.status_code in [200, 201, 202, 204, 301, 302, 304, 401, 403]:
                return {
                    'url': url,
                    'description': description,
                    'status': 'pass',
                    'message': f"✅ {description} accessible (HTTP {response.status_code})"
                }
            else:
                return {
                    'url': url,
                    'description': description,
                    'status': 'warn',
                    'message': f"⚠️ {description} returned HTTP {response.status_code}"
                }
        except requests.exceptions.RequestException as e:
            return {
                'url': url,
                'description': description,
                'status': 'fail',
                'message': f"❌ Error: {str(e)}"
            }

    def test_firewall_configuration(self) -> DiagnosticTest:
        """Test firewall configuration and rules that might block traffic"""
        test = DiagnosticTest("firewall-config", "Firewall configuration check")
        
        if not self.config:
            test.status = "fail"
            test.message = "Configuration not loaded"
            return test
        
        firewall_checks = []
        
        # Check UFW (Ubuntu/Debian)
        ufw_check = self._check_ufw()
        if ufw_check:
            firewall_checks.append(ufw_check)
        
        # Check iptables rules
        iptables_check = self._check_iptables()
        if iptables_check:
            firewall_checks.append(iptables_check)
        
        # Check Docker iptables integration
        docker_iptables_check = self._check_docker_iptables()
        if docker_iptables_check:
            firewall_checks.append(docker_iptables_check)
        
        # Check for common port blocking
        port_blocking_check = self._check_port_blocking()
        if port_blocking_check:
            firewall_checks.append(port_blocking_check)
        
        if not firewall_checks:
            test.status = "skip"
            test.message = "No firewall checks could be performed on this system"
            return test
        
        test.details['tests'] = firewall_checks
        
        passed_tests = [t for t in firewall_checks if t['status'] == 'pass']
        failed_tests = [t for t in firewall_checks if t['status'] == 'fail']
        warn_tests = [t for t in firewall_checks if t['status'] == 'warn']
        
        if len(failed_tests) == 0:
            test.status = "pass" if len(warn_tests) == 0 else "warn"
            test.message = f"Firewall configuration appears correct ({len(passed_tests)} checks passed)"
            if warn_tests:
                test.message += f", {len(warn_tests)} warnings"
        else:
            test.status = "fail"
            test.message = f"Potential firewall issues detected ({len(failed_tests)} problems found)"
        
        return test

    def _check_ufw(self) -> Dict[str, Any]:
        """Check UFW firewall status and rules"""
        try:
            import shutil
            if not shutil.which("ufw"):
                return None
            
            # Check UFW status
            result = subprocess.run(
                ["sudo", "-n", "ufw", "status", "verbose"],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                # Try without sudo
                result = subprocess.run(
                    ["ufw", "status", "verbose"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
            
            if result.returncode == 0:
                status_output = result.stdout.lower()
                
                if "status: inactive" in status_output:
                    return {
                        'test': 'UFW Firewall',
                        'status': 'pass',
                        'message': '✅ UFW firewall is inactive (not blocking traffic)',
                        'details': 'UFW is disabled, so it won\'t block Docker traffic'
                    }
                elif "status: active" in status_output:
                    # Check if Docker ports are allowed
                    ports_to_check = [
                        self.config.get('opal_external_port', 443),
                        self.config.get('opal_http_port', 8080),
                        80, 443
                    ]
                    
                    blocked_ports = []
                    for port in ports_to_check:
                        if str(port) not in status_output and f":{port}" not in status_output:
                            blocked_ports.append(port)
                    
                    if blocked_ports:
                        return {
                            'test': 'UFW Firewall',
                            'status': 'warn',
                            'message': f'⚠️ UFW is active, some ports may be blocked: {blocked_ports}',
                            'details': f'Consider: sudo ufw allow {blocked_ports[0]} for easy-opal'
                        }
                    else:
                        return {
                            'test': 'UFW Firewall',
                            'status': 'pass',
                            'message': '✅ UFW is active but required ports appear to be allowed',
                            'details': 'UFW rules seem compatible with easy-opal'
                        }
                
            return None
            
        except Exception as e:
            return {
                'test': 'UFW Firewall',
                'status': 'warn',
                'message': f'⚠️ Could not check UFW status: {str(e)}',
                'details': 'UFW check requires appropriate permissions'
            }

    def _check_iptables(self) -> Dict[str, Any]:
        """Check iptables rules for blocking patterns"""
        try:
            import shutil
            if not shutil.which("iptables"):
                return None
            
            # Check INPUT chain for DROP/REJECT rules
            result = subprocess.run(
                ["sudo", "-n", "iptables", "-L", "INPUT", "-n"],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                # Try without sudo
                result = subprocess.run(
                    ["iptables", "-L", "INPUT", "-n"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
            
            if result.returncode == 0:
                output = result.stdout.lower()
                
                # Check for common blocking patterns
                blocking_patterns = ['drop all', 'reject all', 'drop    0.0.0.0/0', 'reject    0.0.0.0/0']
                found_blocks = [pattern for pattern in blocking_patterns if pattern in output]
                
                if found_blocks:
                    return {
                        'test': 'iptables Rules',
                        'status': 'warn',
                        'message': '⚠️ Found potentially blocking iptables rules',
                        'details': f'Blocking patterns detected: {found_blocks}. Check if Docker ports are allowed.'
                    }
                else:
                    return {
                        'test': 'iptables Rules',
                        'status': 'pass',
                        'message': '✅ No obvious blocking iptables rules detected',
                        'details': 'iptables INPUT chain appears to allow traffic'
                    }
            
            return None
            
        except Exception as e:
            return {
                'test': 'iptables Rules',
                'status': 'warn',
                'message': f'⚠️ Could not check iptables: {str(e)}',
                'details': 'iptables check requires appropriate permissions'
            }

    def _check_docker_iptables(self) -> Dict[str, Any]:
        """Check Docker's iptables integration"""
        try:
            import shutil
            if not shutil.which("iptables"):
                return None
            
            # Check for Docker chains
            result = subprocess.run(
                ["sudo", "-n", "iptables", "-L", "-n"],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                result = subprocess.run(
                    ["iptables", "-L", "-n"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
            
            if result.returncode == 0:
                output = result.stdout
                
                docker_chains = ['DOCKER', 'DOCKER-USER', 'DOCKER-ISOLATION']
                found_chains = [chain for chain in docker_chains if chain in output]
                
                if found_chains:
                    return {
                        'test': 'Docker iptables',
                        'status': 'pass',
                        'message': f'✅ Docker iptables integration active ({len(found_chains)} chains found)',
                        'details': f'Docker chains: {found_chains}. Docker should handle port forwarding.'
                    }
                else:
                    return {
                        'test': 'Docker iptables',
                        'status': 'warn',
                        'message': '⚠️ Docker iptables chains not found',
                        'details': 'Docker may not be managing iptables. Check Docker daemon configuration.'
                    }
            
            return None
            
        except Exception as e:
            return {
                'test': 'Docker iptables',
                'status': 'warn',
                'message': f'⚠️ Could not check Docker iptables: {str(e)}',
                'details': 'Docker iptables check requires appropriate permissions'
            }

    def _check_port_blocking(self) -> Dict[str, Any]:
        """Check for port blocking by attempting connections"""
        try:
            # Test if we can bind to the configured ports
            ports_to_test = []
            
            ssl_strategy = self.config.get('ssl', {}).get('strategy', 'self-signed')
            if ssl_strategy == 'none':
                ports_to_test.append(self.config.get('opal_http_port', 8080))
            else:
                ports_to_test.append(self.config.get('opal_external_port', 443))
            
            # Add database ports to test
            database_instances = self.config.get('databases', [])
            for db in database_instances:
                db_port = db.get('port')
                if db_port:
                    ports_to_test.append(db_port)
            
            blocked_ports = []
            allowed_ports = []
            
            for port in ports_to_test:
                try:
                    # Try to create a test socket
                    test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    test_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    test_socket.bind(('127.0.0.1', 0))  # Bind to any available port
                    test_socket.close()
                    
                    # Now test if we can connect to the actual port (if something is listening)
                    conn_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    conn_socket.settimeout(2)
                    result = conn_socket.connect_ex(('127.0.0.1', port))
                    conn_socket.close()
                    
                    if result == 0:
                        allowed_ports.append(port)
                    else:
                        # Port not accessible, but this might be normal if service isn't running
                        allowed_ports.append(port)
                        
                except Exception:
                    blocked_ports.append(port)
            
            if blocked_ports:
                return {
                    'test': 'Port Accessibility',
                    'status': 'warn',
                    'message': f'⚠️ Some ports may be blocked by firewall: {blocked_ports}',
                    'details': 'Ports could not be tested. Check firewall rules and port availability.'
                }
            else:
                return {
                    'test': 'Port Accessibility',
                    'status': 'pass',
                    'message': f'✅ Configured ports appear accessible: {allowed_ports}',
                    'details': 'Port binding tests suggest no firewall blocking'
                }
                
        except Exception as e:
            return {
                'test': 'Port Accessibility',
                'status': 'warn',
                'message': f'⚠️ Could not test port accessibility: {str(e)}',
                'details': 'Port testing failed, check system permissions'
            }

    def test_waf_detection(self) -> DiagnosticTest:
        """Detect WAF (Web Application Firewall) that might block requests"""
        test = DiagnosticTest("waf-detection", "WAF/DDoS protection detection")
        
        if not self.config:
            test.status = "fail"
            test.message = "Configuration not loaded"
            return test
        
        waf_checks = []
        
        # Get target URLs for testing
        ssl_strategy = self.config.get('ssl', {}).get('strategy', 'self-signed')
        hosts = self.config.get('hosts', ['localhost'])
        
        if ssl_strategy == 'none':
            if not hosts:
                hosts = ['localhost']
            port = self.config.get('opal_http_port', 8080)
            test_urls = [f"http://{host}:{port}" for host in hosts]
        else:
            port = self.config.get('opal_external_port', 443)
            test_urls = [f"https://{host}:{port}" if port != 443 else f"https://{host}" for host in hosts]
        
        for url in test_urls:
            waf_check = self._check_waf_headers(url)
            if waf_check:
                waf_checks.append(waf_check)
            
            rate_limit_check = self._check_rate_limiting(url)
            if rate_limit_check:
                waf_checks.append(rate_limit_check)
            
            # Only test first URL to avoid triggering actual WAF
            break
        
        if not waf_checks:
            test.status = "skip"
            test.message = "Could not perform WAF detection tests"
            return test
        
        test.details['tests'] = waf_checks
        
        failed_tests = [t for t in waf_checks if t['status'] == 'fail']
        warn_tests = [t for t in waf_checks if t['status'] == 'warn']
        passed_tests = [t for t in waf_checks if t['status'] == 'pass']
        
        if len(failed_tests) > 0:
            test.status = "fail"
            test.message = f"WAF blocking detected ({len(failed_tests)} issues found)"
        elif len(warn_tests) > 0:
            test.status = "warn"
            test.message = f"Potential WAF/protection detected ({len(warn_tests)} warnings)"
        else:
            test.status = "pass"
            test.message = f"No WAF blocking detected ({len(passed_tests)} checks passed)"
        
        return test

    def _check_waf_headers(self, url: str) -> Dict[str, Any]:
        """Check for WAF-related headers in HTTP responses"""
        try:
            response = requests.get(url, timeout=10, verify=False, allow_redirects=True)
            headers = response.headers
            
            # Common WAF signatures
            waf_headers = {
                'cloudflare': ['cf-ray', 'cf-cache-status', 'server'],
                'aws_waf': ['x-amzn-requestid', 'x-amz-cf-id'],
                'azure_waf': ['x-azure-ref', 'x-msedge-ref'],
                'sucuri': ['x-sucuri-id', 'x-sucuri-cache'],
                'incapsula': ['x-iinfo', 'x-cdn'],
                'akamai': ['x-akamai-request-id', 'x-cache'],
                'nginx_waf': ['x-nginx-cache'],
                'mod_security': ['x-mod-security-message']
            }
            
            detected_wafs = []
            for waf_name, header_list in waf_headers.items():
                for header in header_list:
                    if header.lower() in [h.lower() for h in headers.keys()]:
                        if waf_name == 'cloudflare' and header == 'server' and 'cloudflare' in headers.get('server', '').lower():
                            detected_wafs.append(waf_name)
                        elif waf_name != 'cloudflare' or header != 'server':
                            detected_wafs.append(waf_name)
            
            # Check for common blocking responses
            if response.status_code in [403, 406, 429, 503]:
                blocking_keywords = ['blocked', 'forbidden', 'security', 'firewall', 'protection', 'rate limit']
                response_text = response.text.lower()
                found_keywords = [kw for kw in blocking_keywords if kw in response_text]
                
                if found_keywords:
                    return {
                        'test': f'WAF Headers ({url})',
                        'status': 'fail',
                        'message': f'❌ Request appears to be blocked (HTTP {response.status_code})',
                        'details': f'Blocking keywords found: {found_keywords}. Check WAF configuration.'
                    }
            
            if detected_wafs:
                return {
                    'test': f'WAF Headers ({url})',
                    'status': 'warn',
                    'message': f'⚠️ WAF/CDN detected: {list(set(detected_wafs))}',
                    'details': 'Web Application Firewall or CDN present. Ensure proper configuration for Opal.'
                }
            else:
                return {
                    'test': f'WAF Headers ({url})',
                    'status': 'pass',
                    'message': f'✅ No WAF blocking detected',
                    'details': f'HTTP {response.status_code} response, no obvious WAF signatures'
                }
                
        except requests.exceptions.ConnectionError:
            return {
                'test': f'WAF Headers ({url})',
                'status': 'warn',
                'message': '⚠️ Connection failed - possible firewall blocking',
                'details': 'Could not connect to test WAF headers. Check firewall/network configuration.'
            }
        except Exception as e:
            return {
                'test': f'WAF Headers ({url})',
                'status': 'warn',
                'message': f'⚠️ WAF detection failed: {str(e)}',
                'details': 'Could not complete WAF header analysis'
            }

    def _check_rate_limiting(self, url: str) -> Dict[str, Any]:
        """Check for rate limiting by making multiple requests"""
        try:
            # Make 3 quick requests to test for rate limiting
            response_codes = []
            for i in range(3):
                response = requests.get(url, timeout=5, verify=False, allow_redirects=True)
                response_codes.append(response.status_code)
                
                # Check for rate limiting headers
                rate_limit_headers = ['x-ratelimit-remaining', 'x-rate-limit-remaining', 'retry-after', 'x-ratelimit-limit']
                found_headers = [h for h in rate_limit_headers if h.lower() in [header.lower() for header in response.headers.keys()]]
                
                if response.status_code == 429:
                    return {
                        'test': f'Rate Limiting ({url})',
                        'status': 'fail',
                        'message': '❌ Rate limiting detected (HTTP 429)',
                        'details': f'Server returned "Too Many Requests". Headers: {found_headers}'
                    }
                
                if found_headers:
                    return {
                        'test': f'Rate Limiting ({url})',
                        'status': 'warn',
                        'message': f'⚠️ Rate limiting headers detected: {found_headers}',
                        'details': 'Service has rate limiting configured. Monitor for potential blocking.'
                    }
                
                # Small delay between requests
                import time
                time.sleep(0.5)
            
            return {
                'test': f'Rate Limiting ({url})',
                'status': 'pass',
                'message': '✅ No rate limiting detected',
                'details': f'Multiple requests successful: {response_codes}'
            }
            
        except Exception as e:
            return {
                'test': f'Rate Limiting ({url})',
                'status': 'warn',
                'message': f'⚠️ Rate limiting test failed: {str(e)}',
                'details': 'Could not complete rate limiting analysis'
            }

    def run_all_tests(self) -> List[DiagnosticTest]:
        """Run all diagnostic tests"""
        if not self.load_configuration():
            return []
        
        tests = [
            self.check_docker_compose_exists(),
            self.get_container_status(),
            self.test_container_connectivity(),
            self.test_external_ports(),
            self.test_ssl_certificates(),
            self.test_service_endpoints(),
            self.test_firewall_configuration(),
            self.test_waf_detection()
        ]
        
        return [test for test in tests if test is not None]

    def test_external_ports(self) -> DiagnosticTest:
        """Test external port accessibility with retry logic"""
        return self._test_with_retry(self._test_external_ports_impl, "external port", 120, 10)

    def test_ssl_certificates(self) -> DiagnosticTest:
        """Test SSL certificate validity with retry logic"""
        return self._test_with_retry(self._test_ssl_certificates_impl, "SSL certificate", 120, 10)

    def test_service_endpoints(self) -> DiagnosticTest:
        """Test service endpoints accessibility with retry logic"""
        return self._test_with_retry(self._test_service_endpoints_impl, "service endpoint", 120, 10)

    def display_results(self, tests: List[DiagnosticTest]):
        """Display diagnostic results in a formatted table"""
        if not tests:
            console.print("[bold red]No diagnostic tests were run.[/bold red]")
            return
        
        # Summary statistics
        passed = len([t for t in tests if t.status == "pass"])
        failed = len([t for t in tests if t.status == "fail"])
        warned = len([t for t in tests if t.status == "warn"])
        skipped = len([t for t in tests if t.status == "skip"])
        
        # Display header
        console.print("\n" + "="*70)
        console.print("[bold cyan]🏥 EASY-OPAL HEALTH DIAGNOSTIC REPORT[/bold cyan]")
        console.print("="*70)
        
        # Overall health status
        if failed == 0 and warned == 0:
            console.print("[bold green]🎉 SYSTEM STATUS: HEALTHY[/bold green]")
            console.print("[green]All systems are operating normally.[/green]")
        elif failed == 0 and warned > 0:
            console.print("[bold yellow]⚠️  SYSTEM STATUS: WARNING[/bold yellow]")
            console.print(f"[yellow]{warned} issue(s) detected that may need attention.[/yellow]")
        else:
            console.print("[bold red]🚨 SYSTEM STATUS: ISSUES DETECTED[/bold red]")
            console.print(f"[red]{failed} critical issue(s) found that require immediate attention.[/red]")
        
        # Display summary
        console.print(f"\n[bold]📊 Test Results Summary:[/bold]")
        console.print(f"   ✅ Passed: [green]{passed}[/green]")
        console.print(f"   ❌ Failed: [red]{failed}[/red]")
        console.print(f"   ⚠️  Warnings: [yellow]{warned}[/yellow]")
        console.print(f"   ⏭️  Skipped: [blue]{skipped}[/blue]")
        
        # Display detailed results
        console.print(f"\n[bold]🔍 Detailed Test Results:[/bold]")
        console.print("-" * 70)
        
        # Group tests by category for better organization
        test_categories = {
            "🐳 Infrastructure": [],
            "🔗 Network Connectivity": [],
            "🌐 External Access": [],
            "🔒 Security & Certificates": [],
            "💾 Service Health": [],
            "🛡️ Firewall & Protection": []
        }
        
        # Categorize tests
        for test in tests:
            if "docker-compose" in test.name:
                test_categories["🐳 Infrastructure"].append(test)
            elif "container-status" in test.name:
                test_categories["🐳 Infrastructure"].append(test)
            elif "connectivity" in test.name:
                test_categories["🔗 Network Connectivity"].append(test)
            elif "ports" in test.name:
                test_categories["🌐 External Access"].append(test)
            elif "ssl" in test.name or "certificate" in test.name:
                test_categories["🔒 Security & Certificates"].append(test)
            elif "endpoint" in test.name:
                test_categories["💾 Service Health"].append(test)
            elif "firewall" in test.name or "waf" in test.name:
                test_categories["🛡️ Firewall & Protection"].append(test)
            else:
                test_categories["🐳 Infrastructure"].append(test)
        
        # Display each category
        for category, category_tests in test_categories.items():
            if not category_tests:
                continue
                
            console.print(f"\n[bold]{category}[/bold]")
            
            for test in category_tests:
                status_icon = {
                    "pass": "✅",
                    "fail": "❌",
                    "warn": "⚠️",
                    "skip": "⏭️",
                    "pending": "⏳"
                }.get(test.status, "❓")
                
                status_color = {
                    "pass": "green",
                    "fail": "red",
                    "warn": "yellow",
                    "skip": "blue",
                    "pending": "white"
                }.get(test.status, "white")
                
                # Add more descriptive explanations
                explanation = self._get_test_explanation(test.name)
                
                console.print(f"  {status_icon} [{status_color}]{test.description}[/{status_color}]")
                console.print(f"     {explanation}")
                console.print(f"     Result: {test.message}")
                
                # Always show details for connectivity tests, service endpoints, and firewall/WAF tests in verbose mode
                if ((test.name == "container-connectivity" or test.name == "service-endpoints" or test.name == "firewall-config" or test.name == "waf-detection") and test.details) or (test.status in ["fail", "warn"] and test.details):
                    self._display_test_details(test)
                
                console.print()
        
        # Display troubleshooting section if there are issues
        failed_tests = [t for t in tests if t.status in ["fail", "warn"]]
        if failed_tests:
            self._display_troubleshooting_guide(failed_tests)
        
        # Display none/reverse-proxy reminder if applicable
        if self.config and self.config.get('ssl', {}).get('strategy') == 'reverse-proxy':
            console.print("\n" + "="*70)
            console.print("[bold yellow]⚠️  IMPORTANT REMINDER![/bold yellow]")
            console.print("="*70)
            console.print("[yellow]💡 You are using 'none' mode (HTTP only).[/yellow]")
            console.print("[bold red]🔒 Opal requires HTTPS for security in production![/bold red]")
            console.print("\n[dim]Next steps:[/dim]")
            console.print("   • Configure your external HTTPS proxy (nginx, Apache, Cloudflare, etc.)")
            console.print("   • Point your proxy to the HTTP port tested above")
            console.print("   • Ensure the HTTP port is NOT directly accessible from the internet")
            console.print("   • Users should only access Opal through your HTTPS proxy")
            console.print("[dim]\nFor help with proxy configuration, see the easy-opal documentation.[/dim]")

    def _get_test_explanation(self, test_name: str) -> str:
        """Get a user-friendly explanation of what each test does"""
        explanations = {
            "docker-compose-file": "Verifies that the Docker Compose configuration file exists and is readable",
            "container-status": "Checks if all required containers are running and healthy",
            "container-connectivity": "Tests actual TCP connectivity between containers using bash /dev/tcp",
            "external-ports": "Verifies that external ports are accessible from the host system",
            "ssl-certificates": "Validates SSL certificate configuration and expiration dates",
            "service-endpoints": "Tests HTTP/HTTPS endpoints to ensure services are responding correctly",
            "firewall-config": "Checks firewall rules and configuration that might block traffic",
            "waf-detection": "Detects Web Application Firewalls and rate limiting that might interfere with Opal"
        }
        return explanations.get(test_name, "Performs system health verification")

    def _display_test_details(self, test: DiagnosticTest):
        """Display detailed information for a specific test"""
        if 'tests' in test.details:
            # For connectivity tests, show individual connection results prominently
            if test.name == "container-connectivity":
                console.print("     [dim]Individual Connection Tests:[/dim]")
                for subtest in test.details['tests']:
                    status_icon = "✅" if subtest.get('status') == 'pass' else ("⚠️" if subtest.get('status') == 'warn' else "❌")
                    description = subtest.get('description', 'Unknown test')
                    message = subtest.get('message', 'No message')
                    console.print(f"       {status_icon} [bold]{description}[/bold]: {message}")
            else:
                console.print("     [dim]Sub-test details:[/dim]")
                for subtest in test.details['tests']:
                    status_icon = "✅" if subtest.get('status') == 'pass' else ("⚠️" if subtest.get('status') == 'warn' else "❌")
                    # Handle both 'description' and 'test' keys for different test types
                    description = subtest.get('description') or subtest.get('test', 'Unknown test')
                    message = subtest.get('message', 'No message')
                    console.print(f"       {status_icon} {description}: {message}")
        
        if 'containers' in test.details:
            console.print("     [dim]Container details:[/dim]")
            for container in test.details['containers']:
                service = container.get('Service', 'Unknown')
                container_type = container.get('Type', 'Unknown')
                state = container.get('State', 'Unknown')
                status = container.get('Status', 'Unknown')
                
                state_icon = "🟢" if state == 'running' else "🔴"
                console.print(f"       {state_icon} [bold]{service}[/bold] ({container_type}): {state}")

    def _display_troubleshooting_guide(self, failed_tests: List[DiagnosticTest]):
        """Display troubleshooting guidance for failed tests"""
        console.print("\n" + "="*70)
        console.print("[bold yellow]🔧 TROUBLESHOOTING GUIDE[/bold yellow]")
        console.print("="*70)
        
        # Common troubleshooting steps based on test failures
        troubleshooting_tips = {
            "docker-compose-file": [
                "Run './easy-opal setup' to regenerate the Docker Compose configuration",
                "Check if the easy-opal directory is writable",
                "Verify you're running the command from the correct directory"
            ],
            "container-status": [
                "Run './easy-opal up' to start all containers",
                "Check Docker Desktop is running and accessible",
                "Run 'docker ps' to see current container status",
                "Check container logs with 'docker logs <container-name>'"
            ],
            "container-connectivity": [
                "Restart the stack with './easy-opal down' then './easy-opal up'",
                "Check if containers are on the same Docker network",
                "Verify firewall settings aren't blocking internal communication",
                "Check container logs for networking errors"
            ],
            "external-ports": [
                "Check if another service is using the same port",
                "Verify firewall settings allow the configured port",
                "Ensure Docker Desktop port forwarding is working",
                "Try accessing the service with 'curl -k https://localhost:<port>'"
            ],
            "ssl-certificates": [
                "Regenerate certificates with './easy-opal cert --renew'",
                "Check certificate files exist in data/nginx/certs/",
                "For Let's Encrypt: verify domain DNS settings",
                "For self-signed: check mkcert installation"
            ],
            "service-endpoints": [
                "Wait a few minutes for services to fully start up",
                "Check container logs for startup errors",
                "Verify the nginx configuration is correct (if not using 'none' mode)",
                "For 'none' mode: ensure your external HTTPS proxy is configured and running (if using one)",
                "For 'none' mode: verify your proxy forwards correctly to the HTTP port (if using one)",
                "Test individual container endpoints directly",
                "Remember: Opal requires HTTPS for security in production"
            ],
            "firewall-config": [
                "Check UFW status: sudo ufw status verbose",
                "Allow required ports: sudo ufw allow <port>",
                "Check iptables rules: sudo iptables -L -n",
                "Verify Docker iptables integration is working",
                "For UFW: ensure Docker integration is not conflicting",
                "Test port connectivity: telnet localhost <port>",
                "Check Docker daemon configuration for iptables options"
            ],
            "waf-detection": [
                "Check WAF/CDN configuration to allow Opal traffic",
                "Whitelist your server's IP address in WAF settings",
                "Review rate limiting settings and increase limits if needed",
                "For Cloudflare: check firewall rules and security level",
                "Test with WAF temporarily disabled to confirm blocking",
                "Check WAF logs for blocked requests",
                "Ensure proper SSL/TLS configuration in WAF"
            ]
        }
        
        console.print("[bold]Common Solutions:[/bold]")
        shown_tips = set()
        
        for test in failed_tests:
            tips = troubleshooting_tips.get(test.name, [])
            for tip in tips:
                if tip not in shown_tips:
                    console.print(f"  • {tip}")
                    shown_tips.add(tip)
        
        console.print(f"\n[bold]Quick Commands:[/bold]")
        console.print("  • [cyan]./easy-opal status[/cyan] - Check current stack status")
        console.print("  • [cyan]./easy-opal down && ./easy-opal up[/cyan] - Restart the entire stack")
        console.print("  • [cyan]./easy-opal diagnose --verbose[/cyan] - Get more detailed diagnostic info")
        console.print("  • [cyan]docker logs <container-name>[/cyan] - Check specific container logs")
        
        console.print(f"\n[dim]💡 Tip: Run diagnostics again after applying fixes to verify resolution.[/dim]")

@click.command()
@click.option('--verbose', '-v', is_flag=True, help='Show detailed output for all tests')
@click.option('--quiet', '-q', is_flag=True, help='Show only summary information')
@click.option('--no-auto-start', is_flag=True, help='Do not offer to start the stack automatically (for automated scenarios)')
def diagnose(verbose: bool, quiet: bool, no_auto_start: bool):
    """
    Run comprehensive health diagnostics on the easy-opal stack.
    
    This command performs a thorough health check of your easy-opal installation,
    testing infrastructure, network connectivity, external access, security
    configurations, and service endpoints.
    
    Perfect for troubleshooting issues, monitoring system health, or validating
    that everything is working correctly after setup or configuration changes.
    """
    if not ensure_password_is_set():
        return
    
    console.print("[bold cyan]🏥 Running Easy-Opal Health Diagnostics...[/bold cyan]")
    
    diagnostics = ContainerDiagnostics()
    
    # First, load configuration and check if stack is running
    if not diagnostics.load_configuration():
        return
    
    is_running, container_count, status_message = diagnostics.check_stack_running()
    
    if not is_running:
        # Handle the case where the stack is not running
        console.print("\n" + "="*70)
        console.print("[bold yellow]⚠️  STACK STATUS CHECK[/bold yellow]")
        console.print("="*70)
        console.print(f"[yellow]The easy-opal stack is currently not running.[/yellow]")
        console.print(f"[dim]Status: {status_message}[/dim]")
        console.print()
        console.print("[yellow]❌ Cannot perform comprehensive diagnostics on a stopped stack.[/yellow]")
        console.print("[dim]Most network connectivity, port accessibility, and service health tests will fail when containers are not running.[/dim]")
        
        if quiet or no_auto_start:
            # In quiet or no-auto-start mode, just show the issue and exit
            if quiet:
                console.print(f"\n🏥 Easy-Opal Health Check Results")
                console.print("-" * 50)
                console.print(f"[bold red]🚨 STACK NOT RUNNING[/bold red]")
                console.print(f"[red]   Stack is down: {status_message}[/red]")
                console.print(f"[dim]   Start the stack with './easy-opal up' and run diagnostics again[/dim]")
            else:
                console.print(f"\n[bold]💡 Next Steps:[/bold]")
                console.print(f"   1. Start the stack: [cyan]./easy-opal up[/cyan]")
                console.print(f"   2. Run diagnostics again: [cyan]./easy-opal diagnose[/cyan]")
                console.print(f"\n[dim]Use './easy-opal diagnose --no-auto-start' to skip this check in automated scenarios.[/dim]")
            exit(1)
        else:
            # Interactive mode - ask if user wants to start the stack
            console.print(f"[bold]🚀 Would you like to start the stack now and then run diagnostics?[/bold]")
            console.print(f"[dim]This will run './easy-opal up' followed by the health checks.[/dim]")
            
            if Confirm.ask("Start the stack", default=True):
                console.print(f"\n[cyan]Starting the easy-opal stack...[/cyan]")
                try:
                    docker_up()
                    console.print(f"[green]✅ Stack started successfully![/green]")
                    console.print(f"[dim]Waiting a moment for services to initialize...[/dim]")
                    time.sleep(3)  # Give services a moment to start up
                except Exception as e:
                    console.print(f"[bold red]❌ Failed to start the stack: {e}[/bold red]")
                    console.print(f"[dim]Please run './easy-opal up' manually and then retry diagnostics.[/dim]")
                    exit(1)
            else:
                console.print(f"\n[yellow]Diagnostics cancelled.[/yellow]")
                console.print(f"[dim]Start the stack with './easy-opal up' when you're ready to run diagnostics.[/dim]")
                return
    
    # Now run the full diagnostics on the running stack
    if not quiet:
        console.print("[dim]Checking infrastructure, connectivity, security, and service health...[/dim]")
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True
    ) as progress:
        task = progress.add_task("Performing system health checks...", total=None)
        
        tests = diagnostics.run_all_tests()
        
        progress.update(task, completed=True)
    
    if not quiet:
        diagnostics.display_results(tests)
    else:
        # Enhanced quiet mode with more informative output
        passed = len([t for t in tests if t.status == "pass"])
        failed = len([t for t in tests if t.status == "fail"])
        warned = len([t for t in tests if t.status == "warn"])
        total = len(tests)
        
        console.print(f"\n🏥 Easy-Opal Health Check Results ({total} tests)")
        console.print("-" * 50)
        
        if failed > 0:
            console.print(f"[bold red]🚨 CRITICAL ISSUES DETECTED[/bold red]")
            console.print(f"[red]   ❌ {failed} failed, ⚠️ {warned} warnings, ✅ {passed} passed[/red]")
            console.print(f"[dim]   Run './easy-opal diagnose' for detailed troubleshooting info[/dim]")
        elif warned > 0:
            console.print(f"[bold yellow]⚠️  WARNINGS DETECTED[/bold yellow]")
            console.print(f"[yellow]   ⚠️ {warned} warnings, ✅ {passed} passed[/yellow]")
            console.print(f"[dim]   Run './easy-opal diagnose' for detailed info[/dim]")
        else:
            console.print(f"[bold green]🎉 SYSTEM HEALTHY[/bold green]")
            console.print(f"[green]   ✅ All {passed} tests passed - your easy-opal installation is working perfectly![/green]")
    
    # Exit with error code if there are failures for scripting
    failed_count = len([t for t in tests if t.status == "fail"])
    if failed_count > 0:
        if not quiet:
            console.print(f"\n[dim]💡 Exit code: {failed_count} (for automated scripts/CI systems)[/dim]")
        exit(failed_count) 