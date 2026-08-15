#!/usr/bin/env node
import http from "node:http";
import https from "node:https";
import { timingSafeEqual, randomBytes } from "node:crypto";
import { pathToFileURL } from "node:url";

const HOP_BY_HOP_HEADERS = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

const ALLOWED_ROUTES = new Set([
  "GET /v1/models",
  "POST /v1/chat/completions",
]);

const LOOPBACK_HOSTS = new Set(["127.0.0.1", "::1", "[::1]", "localhost"]);

const tokenMatches = (header, expectedToken) => {
  if (typeof header !== "string" || !header.startsWith("Bearer ")) {
    return false;
  }
  const actual = Buffer.from(header.slice("Bearer ".length));
  const expected = Buffer.from(expectedToken);
  return actual.length === expected.length && timingSafeEqual(actual, expected);
};

const copyEndToEndHeaders = (headers, { omitContentLength = false } = {}) => {
  const copied = {};
  for (const [name, value] of Object.entries(headers)) {
    const lowerName = name.toLowerCase();
    if (
      value === undefined ||
      HOP_BY_HOP_HEADERS.has(lowerName) ||
      (omitContentLength && lowerName === "content-length")
    ) {
      continue;
    }
    copied[lowerName] = value;
  }
  return copied;
};

// Fire-and-forget OTLP trace report to a self-hosted Langfuse. Enabled only
// when both AGENT_STATION_LANGFUSE_PUBLIC_KEY and _SECRET_KEY are set; a
// failed report must never affect the relayed completion.
function reportToLangfuse({
  baseUrl,
  publicKey,
  secretKey,
  traceId,
  spanId,
  model,
  startNs,
  endNs,
  inputTokens,
  outputTokens,
  input,
  output,
}) {
  if (!publicKey || !secretKey || !baseUrl) {
    return;
  }
  const attributes = [
    { key: "gen_ai.request.model", value: { stringValue: model ?? "unknown" } },
  ];
  if (inputTokens !== undefined) {
    attributes.push({
      key: "gen_ai.usage.input_tokens",
      value: { intValue: String(inputTokens) },
    });
  }
  if (outputTokens !== undefined) {
    attributes.push({
      key: "gen_ai.usage.output_tokens",
      value: { intValue: String(outputTokens) },
    });
  }
  // Full request/response bodies, mapped by Langfuse's OTLP ingestion into the
  // observation's `input`/`output` fields (langfuse.observation.input/output).
  // Capped generously so a pathological body can never bloat the fire-and-forget
  // report; the relay itself is unaffected either way.
  const MAX_IO_CHARS = 512 * 1024;
  if (input) {
    attributes.push({
      key: "langfuse.observation.input",
      value: { stringValue: input.slice(0, MAX_IO_CHARS) },
    });
  }
  if (output) {
    attributes.push({
      key: "langfuse.observation.output",
      value: { stringValue: output.slice(0, MAX_IO_CHARS) },
    });
  }
  const payload = {
    resourceSpans: [
      {
        resource: {
          attributes: [
            { key: "service.name", value: { stringValue: "dsh-openai-proxy" } },
          ],
        },
        scopeSpans: [
          {
            scope: { name: "openai-proxy" },
            spans: [
              {
                traceId,
                spanId,
                name: "chat.completion",
                kind: 3,
                startTimeUnixNano: String(startNs),
                endTimeUnixNano: String(endNs),
                attributes,
              },
            ],
          },
        ],
      },
    ],
  };
  let parsed;
  try {
    parsed = new URL(baseUrl);
  } catch {
    return;
  }
  const body = JSON.stringify(payload);
  const request = http.request(
    {
      hostname: parsed.hostname,
      port: parsed.port || (parsed.protocol === "https:" ? 443 : 80),
      path: "/api/public/otel/v1/traces",
      method: "POST",
      headers: {
        "content-type": "application/json",
        "content-length": Buffer.byteLength(body),
        authorization:
          "Basic " +
          Buffer.from(`${publicKey}:${secretKey}`).toString("base64"),
      },
      timeout: 5000,
    },
    () => {
      request.resume();
    },
  );
  request.on("error", () => {});
  request.on("timeout", () => request.destroy());
  request.end(body);
}

export function createContentLengthProxy({
  targetHost = "127.0.0.1",
  targetPort = 18092,
  targetUrl,
  apiKey,
  downstreamToken,
  modelOverride,
  maxBodyBytes = 16 * 1024 * 1024,
  requestTimeoutMs = 120_000,
  bufferUpstream = false,
  langfuse = {},
} = {}) {
  const upstreamBase = new URL(
    targetUrl ?? `http://${targetHost}:${targetPort}`,
  );
  if (!new Set(["http:", "https:"]).has(upstreamBase.protocol)) {
    throw new TypeError("proxy target must use http or https");
  }
  if (upstreamBase.username || upstreamBase.password) {
    throw new TypeError("proxy target URL must not contain credentials");
  }
  if (apiKey && !downstreamToken) {
    throw new TypeError(
      "a downstream token is required when the proxy injects a provider key",
    );
  }
  if (
    apiKey &&
    upstreamBase.protocol === "http:" &&
    !LOOPBACK_HOSTS.has(upstreamBase.hostname.toLowerCase())
  ) {
    throw new TypeError(
      "provider credentials require HTTPS for non-loopback targets",
    );
  }
  if (!Number.isSafeInteger(requestTimeoutMs) || requestTimeoutMs <= 0) {
    throw new TypeError("proxy request timeout must be a positive integer");
  }
  const upstreamTransport =
    upstreamBase.protocol === "https:" ? https : http;
  const upstreamBasePath = upstreamBase.pathname.replace(/\/+$/, "");

  return http.createServer((request, response) => {
    const parsedRequestUrl = new URL(
      request.url ?? "/",
      "http://agent-station-proxy.invalid",
    );
    const route = `${request.method ?? "GET"} ${parsedRequestUrl.pathname}`;
    if (!ALLOWED_ROUTES.has(route)) {
      request.resume();
      response.writeHead(404, { "content-type": "application/json" });
      response.end('{"error":"unsupported proxy route"}');
      return;
    }
    if (
      downstreamToken &&
      !tokenMatches(request.headers.authorization, downstreamToken)
    ) {
      request.resume();
      response.writeHead(401, { "content-type": "application/json" });
      response.end('{"error":"unauthorized proxy client"}');
      return;
    }

    // /v1/models is answered locally: the watchdog health check must never
    // depend on upstream latency, otherwise a slow in-flight completion
    // triggers a false relay restart that kills the request mid-flight.
    if (route === "GET /v1/models") {
      request.resume();
      response.writeHead(200, { "content-type": "application/json" });
      response.end(
        JSON.stringify({
          object: "list",
          data: [
            {
              id: "step-3.7-flash",
              object: "model",
              owned_by: "agent-station-openai-proxy",
            },
          ],
        }),
      );
      return;
    }

    const chunks = [];
    let receivedBytes = 0;
    let overflowed = false;

    request.on("data", (chunk) => {
      receivedBytes += chunk.length;
      if (receivedBytes > maxBodyBytes) {
        overflowed = true;
        return;
      }
      chunks.push(chunk);
    });

    request.on("error", () => {
      if (!response.headersSent) {
        response.writeHead(400, { "content-type": "application/json" });
      }
      response.end('{"error":"invalid downstream request"}');
    });

    request.on("end", () => {
      if (overflowed) {
        response.writeHead(413, { "content-type": "application/json" });
        response.end('{"error":"request body exceeds proxy limit"}');
        return;
      }

      let body = Buffer.concat(chunks);
      let downstreamWantsStream = false;
      let reportedModel;
      let reportedInputTokens;
      let reportedInput;
      if (
        request.url?.startsWith("/v1/chat/completions") &&
        request.headers["content-type"]?.includes("application/json")
      ) {
        try {
          const payload = JSON.parse(body.toString("utf8"));
          if (payload.stream === true) downstreamWantsStream = true;
          if (modelOverride) payload.model = modelOverride;
          if (payload.model) reportedModel = payload.model;
          if (Array.isArray(payload.messages)) {
            reportedInputTokens = Math.round(
              JSON.stringify(payload.messages).length / 4,
            );
          }
          if (bufferUpstream && payload.stream === true) {
            // Complete (non-streaming) upstream fetch: long-lived upstream
            // streams are the observed failure mode; a single complete
            // response is relayed back in the client's expected shape.
            payload.stream = false;
          }
          body = Buffer.from(JSON.stringify(payload));
          reportedInput = body.toString("utf8");
        } catch {
          response.writeHead(400, { "content-type": "application/json" });
          response.end('{"error":"invalid OpenAI JSON request"}');
          return;
        }
      }

      const traceId = randomBytes(16).toString("hex");
      const spanId = randomBytes(8).toString("hex");
      const startNs = Date.now().toString() + "000000";

      const headers = copyEndToEndHeaders(request.headers, {
        omitContentLength: true,
      });
      delete headers.authorization;
      headers.host = upstreamBase.host;
      headers["content-length"] = String(body.length);
      if (apiKey) {
        headers.authorization = `Bearer ${apiKey}`;
      }

      const downstreamPath = request.url?.startsWith("/")
        ? request.url
        : `/${request.url ?? ""}`;
      const upstreamPath = `${upstreamBasePath}${downstreamPath}` || "/";

      const upstreamRequest = upstreamTransport.request(
        {
          hostname: upstreamBase.hostname,
          port: upstreamBase.port || undefined,
          method: request.method,
          path: upstreamPath,
          headers,
        },
        (upstreamResponse) => {
          if (!bufferUpstream || !downstreamWantsStream) {
            const responseHeaders = copyEndToEndHeaders(upstreamResponse.headers);
            response.writeHead(upstreamResponse.statusCode ?? 502, responseHeaders);
            upstreamResponse.pipe(response);
            return;
          }
          // Buffered relay: collect the complete upstream body, then emit it
          // in the downstream client's expected streaming shape.
          const upstreamChunks = [];
          upstreamResponse.on("data", (chunk) => upstreamChunks.push(chunk));
          upstreamResponse.on("end", () => {
            const complete = Buffer.concat(upstreamChunks);
            try {
              const parsedCompletion = JSON.parse(complete.toString("utf8"));
              reportToLangfuse({
                baseUrl: langfuse.baseUrl,
                publicKey: langfuse.publicKey,
                secretKey: langfuse.secretKey,
                traceId,
                spanId,
                model: parsedCompletion.model ?? reportedModel,
                startNs,
                endNs: Date.now().toString() + "000000",
                inputTokens:
                  parsedCompletion.usage?.prompt_tokens ?? reportedInputTokens,
                outputTokens: parsedCompletion.usage?.completion_tokens,
                input: reportedInput,
                output: complete.toString("utf8"),
              });
            } catch {
              // Report failures never affect the relayed response.
            }
            if (
              response.headersSent ||
              (upstreamResponse.statusCode ?? 0) >= 400
            ) {
              if (!response.headersSent) {
                response.writeHead(upstreamResponse.statusCode ?? 502, {
                  "content-type": "application/json",
                });
              }
              response.end(complete);
              return;
            }
            response.writeHead(200, {
              "content-type": "text/event-stream",
              "cache-control": "no-cache",
              connection: "keep-alive",
            });
            // The buffered upstream body is a COMPLETE (non-streaming)
            // chat.completion JSON. OpenAI-compatible streaming clients
            // (agentscope/openai) parse chunks that carry choice.delta, and
            // yield nothing from a full message object. Re-shape the single
            // complete response into standard streaming chunks: reasoning
            // delta, content delta, finish chunk with usage, then [DONE].
            try {
              const full = JSON.parse(complete.toString("utf8"));
              const base = {
                id: full.id,
                object: "chat.completion.chunk",
                created: full.created,
                model: full.model,
              };
              const emit = (obj) => {
                response.write(`data: ${JSON.stringify(obj)}\n\n`);
              };
              for (const choice of full.choices ?? []) {
                const message = choice.message ?? {};
                const index = choice.index ?? 0;
                if (
                  typeof message.reasoning_content === "string" &&
                  message.reasoning_content.length > 0
                ) {
                  emit({
                    ...base,
                    choices: [
                      {
                        index,
                        delta: {
                          reasoning_content: message.reasoning_content,
                        },
                        finish_reason: null,
                      },
                    ],
                  });
                }
                if (typeof message.reasoning === "string" && message.reasoning.length > 0) {
                  emit({
                    ...base,
                    choices: [
                      {
                        index,
                        delta: { reasoning_content: message.reasoning },
                        finish_reason: null,
                      },
                    ],
                  });
                }
                if (typeof message.content === "string" && message.content.length > 0) {
                  emit({
                    ...base,
                    choices: [
                      {
                        index,
                        delta: { content: message.content },
                        finish_reason: null,
                      },
                    ],
                  });
                }
                if (Array.isArray(message.tool_calls) && message.tool_calls.length > 0) {
                  for (const tc of message.tool_calls) {
                    emit({
                      ...base,
                      choices: [
                        {
                          index,
                          delta: {
                            tool_calls: [
                              {
                                index: tc.index ?? 0,
                                id: tc.id,
                                type: tc.type ?? "function",
                                function: tc.function,
                              },
                            ],
                          },
                          finish_reason: null,
                        },
                      ],
                    });
                  }
                }
                emit({
                  ...base,
                  choices: [
                    {
                      index,
                      delta: {},
                      finish_reason: choice.finish_reason ?? "stop",
                    },
                  ],
                });
              }
              if (full.usage) {
                emit({ ...base, choices: [], usage: full.usage });
              }
            } catch {
              // Fall back to the single-chunk relay when the body is not
              // parseable JSON.
              response.write(`data: ${complete.toString("utf8")}\n\n`);
            }
            response.write("data: [DONE]\n\n");
            response.end();
          });
          upstreamResponse.on("error", () => {
            if (!response.headersSent) {
              response.writeHead(502, { "content-type": "application/json" });
            }
            response.end('{"error":"upstream body read failed"}');
          });
        },
      );

      upstreamRequest.on("error", () => {
        if (!response.headersSent) {
          response.writeHead(502, { "content-type": "application/json" });
        }
        response.end('{"error":"OpenAI-compatible upstream unavailable"}');
      });
      upstreamRequest.setTimeout(requestTimeoutMs, () => {
        upstreamRequest.destroy(new Error("upstream request timed out"));
      });
      response.on("close", () => {
        if (!response.writableEnded) {
          upstreamRequest.destroy(new Error("downstream client disconnected"));
        }
      });
      upstreamRequest.end(body);
    });
  });
}

const isEntrypoint =
  process.argv[1] !== undefined &&
  import.meta.url === pathToFileURL(process.argv[1]).href;

if (isEntrypoint) {
  const listenHost = process.env.AGENT_STATION_PROXY_HOST ?? "127.0.0.1";
  const listenPort = Number(process.env.AGENT_STATION_PROXY_PORT ?? "18090");
  const targetHost = process.env.AGENT_STATION_PROXY_TARGET_HOST ?? "127.0.0.1";
  const targetPort = Number(
    process.env.AGENT_STATION_PROXY_TARGET_PORT ?? "18092",
  );
  const targetUrl = process.env.AGENT_STATION_PROXY_TARGET_URL;
  const apiKey = process.env.AGENT_STATION_PROXY_API_KEY;
  const downstreamToken = process.env.AGENT_STATION_PROXY_DOWNSTREAM_TOKEN;
  const modelOverride = process.env.AGENT_STATION_PROXY_MODEL;
  const bufferUpstream = process.env.AGENT_STATION_PROXY_BUFFER === "1";
  const langfuse = {
    baseUrl:
      process.env.AGENT_STATION_LANGFUSE_HOST ?? "http://127.0.0.1:3001",
    publicKey: process.env.AGENT_STATION_LANGFUSE_PUBLIC_KEY,
    secretKey: process.env.AGENT_STATION_LANGFUSE_SECRET_KEY,
  };
  const server = createContentLengthProxy({
    targetHost,
    targetPort,
    targetUrl,
    apiKey,
    downstreamToken,
    modelOverride,
    bufferUpstream,
    langfuse,
    requestTimeoutMs: Number(process.env.AGENT_STATION_PROXY_TIMEOUT_MS ?? 1_800_000),
  });

  server.listen(listenPort, listenHost, () => {
    const safeTarget = new URL(
      targetUrl ?? `http://${targetHost}:${targetPort}`,
    );
    process.stderr.write(
      `OpenAI compatibility proxy listening on ${listenHost}:${listenPort} -> ${safeTarget.origin}${safeTarget.pathname}\n`,
    );
  });
}
