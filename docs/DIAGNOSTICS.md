# Easy-Opal Diagnostics Guide

The `diagnose` command provides comprehensive health monitoring and troubleshooting capabilities for your easy-opal installation. This guide covers all aspects of the diagnostic system.

## Overview

The diagnostic system performs systematic health checks across six critical areas:

1. **🐳 Infrastructure** - Docker Compose configuration and container status
2. **🔗 Network Connectivity** - Inter-container communication testing  
3. **🌐 External Access** - Port accessibility from host system
4. **🔒 Security & Certificates** - SSL certificate validation and expiration
5. **💾 Service Health** - HTTP/HTTPS endpoint responses and service availability
6. **🔧 Automated Troubleshooting** - Context-aware guidance for resolving issues

## Usage

### Basic Usage

```bash
# Full diagnostic report with troubleshooting guidance
./easy-opal diagnose

# Quick health check for monitoring/scripting
./easy-opal diagnose --quiet

# Detailed output with additional debugging info (future feature)
./easy-opal diagnose --verbose
```

### Command Options

| Option | Description | Use Case |
|--------|-------------|----------|
| `--quiet` / `-q` | Summary-only output | CI/CD, monitoring scripts, quick checks |
| `--verbose` / `-v` | Detailed debugging output | Deep troubleshooting, support tickets |
| `--no-auto-start` | Disable automatic stack startup prompts | Automated scenarios, scripting |
| (none) | Full interactive report | General troubleshooting, manual validation |

## Smart Stack Detection

The diagnostic system intelligently detects when the easy-opal stack is not running and handles this scenario gracefully instead of showing misleading test failures.

### When Stack is Down

If no containers are running or only partial containers are running, the diagnostic tool will:

1. **Clearly Identify the Issue**: Shows a dedicated "STACK STATUS CHECK" section explaining exactly what's wrong
2. **Prevent Misleading Results**: Avoids running tests that will obviously fail when containers are down
3. **Provide Contextual Guidance**: Offers specific next steps based on the mode:

**Interactive Mode (default):**
```
======================================================================
⚠️  STACK STATUS CHECK
======================================================================
The easy-opal stack is currently not running.
Status: No containers found for this project

❌ Cannot perform comprehensive diagnostics on a stopped stack.
Most network connectivity, port accessibility, and service health tests will fail when containers are not running.

🚀 Would you like to start the stack now and then run diagnostics?
This will run './easy-opal up' followed by the health checks.
Start the stack [y/n] (y): 
```

**Quiet Mode (`--quiet`):**
```
🏥 Easy-Opal Health Check Results
--------------------------------------------------
🚨 STACK NOT RUNNING
   Stack is down: No containers found for this project
   Start the stack with './easy-opal up' and run diagnostics again
```

**No Auto-Start Mode (`--no-auto-start`):**
```
💡 Next Steps:
   1. Start the stack: ./easy-opal up
   2. Run diagnostics again: ./easy-opal diagnose
```

### Automatic Stack Startup

In interactive mode, if you choose to start the stack:
- Runs `./easy-opal up` automatically
- Waits for services to initialize (3-second delay)
- Continues with full diagnostics on the running stack
- Provides clear feedback on startup success/failure

## Output Formats

### Full Diagnostic Report

When run without flags, provides a comprehensive health report:

```
======================================================================
🏥 EASY-OPAL HEALTH DIAGNOSTIC REPORT
======================================================================
🎉 SYSTEM STATUS: HEALTHY
All systems are operating normally.

📊 Test Results Summary:
   ✅ Passed: 6
   ❌ Failed: 0
   ⚠️  Warnings: 0
   ⏭️  Skipped: 0

🔍 Detailed Test Results:
----------------------------------------------------------------------

🐳 Infrastructure
  ✅ Docker Compose file exists
     Verifies that the Docker Compose configuration file exists and is readable
     Result: Found at docker-compose.yml

  ✅ Container status check
     Checks if all required containers are running and healthy
     Result: All 4 containers are running

🔗 Network Connectivity
  ✅ Inter-container network connectivity
     Tests network communication between containers (Opal↔MongoDB, Nginx↔Opal, etc.)
     Result: All 2 connectivity tests passed

[... additional test categories ...]
```

### Quiet Mode Output

Perfect for automated systems and monitoring:

```bash
./easy-opal diagnose --quiet
```

**Healthy System:**
```
🏥 Easy-Opal Health Check Results (6 tests)
--------------------------------------------------
🎉 SYSTEM HEALTHY
   ✅ All 6 tests passed - your easy-opal installation is working perfectly!
```

**System with Issues:**
```
🏥 Easy-Opal Health Check Results (6 tests)
--------------------------------------------------
🚨 CRITICAL ISSUES DETECTED
   ❌ 2 failed, ⚠️ 1 warnings, ✅ 3 passed
   Run './easy-opal diagnose' for detailed troubleshooting info
```

## Detailed Test Categories

### 🐳 Infrastructure Tests

**Docker Compose File Exists**
- **Purpose**: Verifies the `docker-compose.yml` file is present and readable
- **Common Issues**: Missing file after incomplete setup
- **Solutions**: Run `./easy-opal setup` to regenerate

**Container Status Check**  
- **Purpose**: Ensures all required containers are running and healthy
- **Checks**: MongoDB, Opal, Nginx, Rock containers (if configured)
- **Common Issues**: Containers stopped, failed to start, resource constraints
- **Solutions**: `./easy-opal up`, check Docker Desktop, review container logs

### 🔗 Network Connectivity Tests

**Inter-container Network Connectivity**
- **Purpose**: Tests actual TCP communication between critical container pairs
- **Tests Performed**:
  - Opal → MongoDB (port 27017)
  - Nginx → Opal (port 8080)
  - Opal → Rock containers (port 8085)
- **Method**: **Bash /dev/tcp** - Built-in TCP connectivity testing available in all standard containers
- **How it works**: Uses `bash -c 'timeout 5 bash -c "</dev/tcp/target/port"'` to establish actual TCP connections
- **Retry Logic**: Automatically waits up to 2 minutes for services to start if initial connectivity tests fail
  - Retries every 10 seconds for failed tests only
  - Shows progress: "⏳ 2 connectivity test(s) failed. Waiting 10s for services to start... (attempt 1, 120s remaining)"
  - Reports final status: "✅ All connectivity tests passed after 11s!" or "⏱️ Connectivity testing completed after 120s. 1 test(s) still failing."
- **Common Issues**: Network isolation, firewall blocking, container startup timing, service not listening on expected ports
- **Solutions**: Restart stack, check Docker networks, verify container logs, check service startup completion

### 🌐 External Access Tests

**External Port Accessibility**
- **Purpose**: Verifies configured ports are accessible from the host system
- **Tests**: HTTPS port (default 8443), HTTP port (reverse-proxy mode only)
- **Retry Logic**: Automatically waits up to 2 minutes for ports to become accessible if initial tests fail
  - Retries every 10 seconds for failed port tests only
  - Shows progress: "⏳ 1 external port test(s) failed. Waiting 10s for services to fully start... (attempt 1, 120s remaining)"
- **Common Issues**: Port conflicts, firewall blocking, incorrect configuration, service startup delays
- **Solutions**: Check port availability, verify firewall rules, test with curl, allow time for startup

### 🔒 Security & Certificate Tests

**SSL Certificate Validation**
- **Purpose**: Validates SSL certificates for all configured hosts
- **Checks**: Certificate existence, expiration dates, basic validation
- **Certificate Types Supported**:
  - Self-signed certificates (via mkcert)
  - Let's Encrypt certificates
  - Manual certificates
- **Retry Logic**: Automatically waits up to 2 minutes for SSL services to become available if initial tests fail
  - Retries every 10 seconds for failed certificate tests only
  - Shows progress: "⏳ 1 SSL certificate test(s) failed. Waiting 10s for services to fully start... (attempt 1, 120s remaining)"
- **Common Issues**: Expired certificates, missing certificate files, DNS mismatches, service startup delays
- **Solutions**: Regenerate certificates, check file permissions, verify DNS settings, allow time for SSL service startup

### 💾 Service Health Tests

**Service Endpoint Health Checks**
- **Purpose**: Tests actual HTTP/HTTPS responses from services
- **Endpoints Tested**:
  - Opal web interface (`/`)
  - Opal API endpoint (`/ws`)
- **Retry Logic**: Automatically waits up to 2 minutes for web services to become accessible if initial tests fail
  - Retries every 10 seconds for failed endpoint tests only
  - Shows progress: "⏳ 1 service endpoint test(s) failed. Waiting 10s for services to fully start... (attempt 1, 120s remaining)"
  - Reports final status: "✅ All service endpoint tests passed after 26s!" or timeout notification
- **Common Issues**: Services not fully started, configuration errors, certificate issues, web server startup delays
- **Solutions**: Wait for startup completion, check service logs, verify certificates, allow time for web service initialization

## Troubleshooting Integration

### Automated Guidance

When issues are detected, the diagnostic system provides contextual troubleshooting guidance:

```
======================================================================
🔧 TROUBLESHOOTING GUIDE
======================================================================

Common Solutions:
  • Run './easy-opal up' to start all containers
  • Check Docker Desktop is running and accessible  
  • Restart the stack with './easy-opal down' then './easy-opal up'
  • Check if another service is using the same port

Quick Commands:
  • ./easy-opal status - Check current stack status
  • ./easy-opal down && ./easy-opal up - Restart the entire stack
  • ./easy-opal diagnose --verbose - Get more detailed diagnostic info
  • docker logs <container-name> - Check specific container logs

💡 Tip: Run diagnostics again after applying fixes to verify resolution.
```

### Common Issue Patterns

**Stack Not Running**
- **Symptoms**: Container status failures, port accessibility failures, endpoint failures
- **Quick Fix**: `./easy-opal up`
- **Investigation**: Check Docker Desktop, container logs, resource usage

**Network Issues**
- **Symptoms**: Container connectivity failures, partial service access
- **Quick Fix**: `./easy-opal down && ./easy-opal up`
- **Investigation**: Docker network inspection, firewall settings

**Certificate Problems**
- **Symptoms**: SSL validation failures, HTTPS access issues
- **Quick Fix**: `./easy-opal cert regenerate`
- **Investigation**: Certificate file permissions, expiration dates, DNS settings

**Port Conflicts**
- **Symptoms**: External port access failures, container startup failures
- **Quick Fix**: Change port in configuration, stop conflicting services
- **Investigation**: `netstat -an | grep <port>`, check other Docker containers

## Integration with Other Systems

### CI/CD Pipeline Integration

The diagnostic command is designed for seamless CI/CD integration:

```yaml
# GitHub Actions example
- name: Health Check
  run: |
    ./easy-opal diagnose --quiet --no-auto-start
    # Exit code will be non-zero if any tests fail
    # --no-auto-start prevents interactive prompts in automated environments
```

```bash
# Jenkins/Shell script example
if ./easy-opal diagnose --quiet --no-auto-start; then
    echo "System healthy, proceeding with deployment"
else
    echo "Health check failed, aborting deployment"
    exit 1
fi
```

### Monitoring System Integration

**Exit Codes**: Returns the number of failed tests (0 = success), making it perfect for monitoring systems.

**Parsing Output**: Quiet mode provides structured output that's easy to parse programmatically.

**Health Check Endpoints**: Consider running diagnostics periodically and exposing results via your monitoring system.

### Log Integration

**Structured Logging**: The diagnostic output is designed to be human-readable while maintaining structure for log parsing.

**Error Context**: Each failure includes detailed context for log analysis and alerting.

## Performance Considerations

### Execution Time

- **Typical Runtime**: 5-15 seconds depending on system performance and network latency
- **Timeout Settings**: Individual tests have 10-second timeouts to prevent hanging
- **Resource Usage**: Minimal CPU and memory impact during execution

### Frequency Recommendations

- **Development**: Run after any configuration changes
- **Staging**: Include in deployment pipelines
- **Production**: Schedule every 15-30 minutes for continuous monitoring
- **Troubleshooting**: Run before and after applying fixes

## Advanced Usage

### Exit Code Handling

```bash
# Advanced scripting with exit codes
./easy-opal diagnose --quiet
EXIT_CODE=$?

case $EXIT_CODE in
    0)
        echo "All systems healthy"
        ;;
    1)
        echo "Minor issues detected, continuing"
        ;;
    [2-5])
        echo "Multiple issues detected, investigating"
        ./easy-opal diagnose  # Full output for investigation
        ;;
    *)
        echo "Critical system failure"
        exit 1
        ;;
esac
```

### Monitoring Integration Examples

**Nagios/Icinga:**
```bash
#!/bin/bash
# nagios_check_easy_opal.sh
if ./easy-opal diagnose --quiet > /dev/null 2>&1; then
    echo "OK - Easy-Opal system healthy"
    exit 0
else
    echo "CRITICAL - Easy-Opal health check failed"
    exit 2
fi
```

**Prometheus/Grafana:**
```bash
#!/bin/bash
# Export metrics for Prometheus
RESULT=$(./easy-opal diagnose --quiet 2>/dev/null | tail -1)
if echo "$RESULT" | grep -q "SYSTEM HEALTHY"; then
    echo "easy_opal_health_status 1"
else
    echo "easy_opal_health_status 0"
fi
```

## Troubleshooting the Diagnostics

### When Diagnostics Fail to Run

**Permission Issues:**
```bash
# Ensure execute permissions
chmod +x easy-opal

# Check Python environment
python --version
which python
```

**Environment Issues:**
```bash
# Verify Poetry environment
poetry env info
poetry install
```

**Docker Issues:**
```bash
# Test Docker access
docker ps
docker version
```

### False Positives/Negatives

**Network Timing Issues**: If connectivity tests fail intermittently, containers may still be starting up. Wait a few minutes and re-run.

**Certificate Warnings**: Self-signed certificate warnings are expected and don't indicate actual problems.

**Port Accessibility**: On some systems, Docker port forwarding may take time to fully initialize.

## Support and Feedback

If you encounter issues with the diagnostic system:

1. **Run with full output**: `./easy-opal diagnose` for complete information
2. **Check logs**: Review container logs with `docker logs <container-name>`
3. **Verify environment**: Ensure Docker Desktop is running and accessible
4. **Report issues**: Include diagnostic output when reporting problems

The diagnostic system is continuously improved based on real-world usage patterns and feedback from the community. 