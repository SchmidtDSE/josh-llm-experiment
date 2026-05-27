#!/usr/bin/env bash
# Install the Josh CLI: download the Josh fat jar, record its sha256 as the
# reproducibility anchor (SchmidtDSE/josh has no tagged releases), drop a
# wrapper at /usr/local/bin/josh so the CLI is on PATH.
#
# Defaults to the rolling DEV build: it carries the `mcp` subcommand
# (SchmidtDSE/josh#440), which `main` lacks until dev->main lands (not
# soon). The Dockerfile passes JOSH_JAR_URL with the same default; override
# it there with --build-arg to pin a different build. Idempotent:
# re-running overwrites the jar with the current rolling build.

set -euo pipefail

JOSH_JAR_URL="${JOSH_JAR_URL:-https://joshsim.org/dist/dev/joshsim-fat.jar}"
JOSH_HOME="${JOSH_HOME:-/opt/josh}"

mkdir -p "$JOSH_HOME"
curl -fSL "$JOSH_JAR_URL" -o "$JOSH_HOME/joshsim-fat.jar"
sha256sum "$JOSH_HOME/joshsim-fat.jar" > "$JOSH_HOME/joshsim-fat.jar.sha256"
cat "$JOSH_HOME/joshsim-fat.jar.sha256"

cat > /usr/local/bin/josh <<'WRAPPER'
#!/usr/bin/env bash
# Thin wrapper around the Josh CLI fat jar. JAVA_OPTS is expanded
# unquoted so agents can override JVM defaults per-invocation, e.g.
#   JAVA_OPTS="-Xmx32g -Xss4m" josh preprocess ...
# The container also ships ENV JAVA_TOOL_OPTIONS="-Xmx16g" (see
# Dockerfile) so bare `josh ...` already gets a sane heap; JAVA_OPTS
# is the per-invocation escape hatch.
set -euo pipefail
exec java ${JAVA_OPTS:-} -jar /opt/josh/joshsim-fat.jar "$@"
WRAPPER
chmod +x /usr/local/bin/josh
