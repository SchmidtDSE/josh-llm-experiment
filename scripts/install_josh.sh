#!/usr/bin/env bash
# Install the Josh CLI: download the prod fat jar, record its sha256 as the
# reproducibility anchor (SchmidtDSE/josh has no tagged releases), drop a
# wrapper at /usr/local/bin/josh so the CLI is on PATH.
#
# Invoked from the Dockerfile during image build. Idempotent: re-running
# overwrites the jar with the current rolling-main build.

set -euo pipefail

JOSH_JAR_URL="${JOSH_JAR_URL:-https://joshsim.org/dist/main/joshsim-fat.jar}"
JOSH_HOME="${JOSH_HOME:-/opt/josh}"

mkdir -p "$JOSH_HOME"
curl -fSL "$JOSH_JAR_URL" -o "$JOSH_HOME/joshsim-fat.jar"
sha256sum "$JOSH_HOME/joshsim-fat.jar" > "$JOSH_HOME/joshsim-fat.jar.sha256"
cat "$JOSH_HOME/joshsim-fat.jar.sha256"

cat > /usr/local/bin/josh <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
exec java -jar /opt/josh/joshsim-fat.jar "$@"
WRAPPER
chmod +x /usr/local/bin/josh
