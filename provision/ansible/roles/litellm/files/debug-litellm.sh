#!/bin/bash
# LiteLLM Debug Script
# Helps diagnose startup and configuration issues

set -e

echo "=========================================="
echo "LiteLLM Debug Information"
echo "=========================================="
echo ""

# 1. Check config YAML syntax
echo "1. Validating YAML config syntax..."
python3 << 'EOF'
import yaml
try:
    with open('/etc/litellm/config.yaml', 'r') as f:
        config = yaml.safe_load(f)
    print("   ✓ YAML syntax is valid")
    
    # Check critical settings
    gs = config.get('general_settings', {})
    print(f"   ✓ master_key: {'SET' if gs.get('master_key') else 'NOT SET'}")
    print(f"   ✓ public_routes: {gs.get('public_routes', 'NOT SET')}")
    print(f"   ✓ database_url: {'SET' if gs.get('database_url') else 'NOT SET'}")
    print(f"   ✓ store_model_in_db: {gs.get('store_model_in_db', 'NOT SET')}")
    
except yaml.YAMLError as e:
    print(f"   ✗ YAML syntax error: {e}")
    exit(1)
except Exception as e:
    print(f"   ✗ Error reading config: {e}")
    exit(1)
EOF
echo ""

# 2. Check environment file
echo "2. Checking environment file..."
if [ -f /etc/default/litellm ]; then
    echo "   ✓ Environment file exists"
    grep -E "^(DATABASE|LITELLM)" /etc/default/litellm | sed 's/=.*/=***/' || echo "   - No DATABASE/LITELLM vars"
else
    echo "   ✗ Environment file not found"
fi
echo ""

# 3. Check file permissions
echo "3. Checking file permissions..."
ls -la /etc/litellm/config.yaml | awk '{print "   Config:", $1, $3":"$4}'
ls -la /etc/default/litellm 2>/dev/null | awk '{print "   Env file:", $1, $3":"$4}' || echo "   Env file: not found"
ls -ld /var/lib/litellm | awk '{print "   Working dir:", $1, $3":"$4}'
echo ""

# 4. Check virtual environment
echo "4. Checking virtual environment..."
if [ -d /opt/litellm/venv ]; then
    echo "   ✓ Virtual environment exists"
    source /opt/litellm/venv/bin/activate
    python3 << 'EOF'
import sys
try:
    import litellm
    print(f"   ✓ LiteLLM version: {litellm.__version__}")
except:
    print("   ✗ Cannot import litellm")
    
try:
    from litellm.proxy.utils import PrismaClient
    print("   ✓ PrismaClient available")
except:
    print("   ✗ PrismaClient not available")
EOF
    deactivate
else
    echo "   ✗ Virtual environment not found"
fi
echo ""

# 5. Test database connection (if configured)
echo "5. Testing database connection..."
if grep -q "^DATABASE_URL=" /etc/default/litellm 2>/dev/null; then
    DB_URL=$(grep "^DATABASE_URL=" /etc/default/litellm | cut -d= -f2-)
    if command -v psql &> /dev/null; then
        if echo "\q" | psql "$DB_URL" 2>/dev/null; then
            echo "   ✓ Database connection successful"
        else
            echo "   ✗ Database connection failed"
        fi
    else
        echo "   - psql not installed, skipping DB test"
    fi
else
    echo "   - No DATABASE_URL configured"
fi
echo ""

# 6. Check service status
echo "6. Service status..."
if systemctl is-active --quiet litellm; then
    echo "   ✓ Service is running"
    echo "   Last 5 log lines:"
    journalctl -u litellm -n 5 --no-pager | sed 's/^/     /'
else
    echo "   ✗ Service is not running"
    echo "   Last 10 log lines:"
    journalctl -u litellm -n 10 --no-pager | sed 's/^/     /'
fi
echo ""

# 7. Test health endpoint
echo "7. Testing health endpoint..."
if curl -s -f http://localhost:4000/health > /dev/null 2>&1; then
    echo "   ✓ Health endpoint responding"
    curl -s http://localhost:4000/health | python3 -m json.tool | sed 's/^/     /' || true
else
    echo "   ✗ Health endpoint not responding"
    RESPONSE=$(curl -s -w "\n%{http_code}" http://localhost:4000/health 2>/dev/null | tail -1)
    echo "   HTTP Status: $RESPONSE"
fi
echo ""

echo "=========================================="
echo "Debug complete"
echo "=========================================="

