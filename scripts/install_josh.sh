#!/usr/bin/env bash
# Install the Josh CLI: download the Josh fat jar, verify it against a pinned
# sha256, and drop a wrapper at /usr/local/bin/josh so the CLI is on PATH.
#
# Defaults to the rolling DEV build (it carries the `mcp` subcommand,
# SchmidtDSE/josh#440, which `main` lacks). The jar is a ROLLING artifact at
# a fixed URL, so the Dockerfile pins JOSH_JAR_SHA256 and passes it here —
# that does double duty: (1) reproducibility/integrity (fail if the rolling
# jar isn't the build we expect), and (2) cache-busting — the `RUN` layer is
# cached by command, not by remote content, so without a changing pin a
# rebuild silently keeps a STALE jar. Bump JOSH_JAR_SHA256 in the Dockerfile
# to intentionally move to a newer dev build.

set -euo pipefail

JOSH_JAR_URL="${JOSH_JAR_URL:-https://joshsim.org/dist/dev/joshsim-fat.jar}"
JOSH_JAR_SHA256="${JOSH_JAR_SHA256:-}"
JOSH_HOME="${JOSH_HOME:-/opt/josh}"

mkdir -p "$JOSH_HOME"
curl -fSL "$JOSH_JAR_URL" -o "$JOSH_HOME/joshsim-fat.jar"
ACTUAL_SHA="$(sha256sum "$JOSH_HOME/joshsim-fat.jar" | awk '{print $1}')"
if [ -n "$JOSH_JAR_SHA256" ] && [ "$ACTUAL_SHA" != "$JOSH_JAR_SHA256" ]; then
  echo "ERROR: joshsim-fat.jar sha256 mismatch from $JOSH_JAR_URL" >&2
  echo "  expected (pinned): $JOSH_JAR_SHA256" >&2
  echo "  actual:            $ACTUAL_SHA" >&2
  exit 1
fi
echo "$ACTUAL_SHA  joshsim-fat.jar" > "$JOSH_HOME/joshsim-fat.jar.sha256"
echo "Josh jar sha256: $ACTUAL_SHA (pin ${JOSH_JAR_SHA256:-<unset>})"

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
