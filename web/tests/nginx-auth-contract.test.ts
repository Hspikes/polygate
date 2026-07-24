import { describe, expect, it } from "vitest";
import dockerfile from "../Dockerfile?raw";
import nginxConfig from "../nginx/default.conf?raw";

function locationBody(path: string): string {
  const escaped = path.replaceAll("/", "\\/");
  const match = nginxConfig.match(new RegExp(`location ${escaped} \\{([\\s\\S]*?)\\n    \\}`));
  if (!match) throw new Error(`location ${path} is missing`);
  return match[1];
}

describe("Nginx Web authentication boundary", () => {
  it("overwrites /api credentials with the runtime-only Web identity", () => {
    expect(locationBody("/api/")).toContain(
      "proxy_set_header Authorization $web_gateway_authorization;",
    );
    expect(nginxConfig).toContain('map "${WEB_GATEWAY_API_KEY}"');
    expect(dockerfile).toContain("/etc/nginx/templates/default.conf.template");
    expect(dockerfile).toContain('NGINX_ENVSUBST_FILTER="^WEB_GATEWAY_API_KEY$"');
  });

  it("continues to forward each public /v1 client's own credential", () => {
    expect(locationBody("/v1/")).toContain(
      "proxy_set_header Authorization $http_authorization;",
    );
  });
});
