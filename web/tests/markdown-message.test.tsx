import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MarkdownMessage } from "../src/components/MarkdownMessage";

describe("MarkdownMessage", () => {
  it("renders GFM tables, lists and fenced code", () => {
    const { container } = render(
      <MarkdownMessage content={"- one\n- two\n\n|A|B|\n|-|-|\n|1|2|\n\n```ts\nconst ok = true;\n```"} />,
    );
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByText("one")).toBeInTheDocument();
    expect(container.querySelector("code.language-ts")).toHaveTextContent("const ok = true;");
  });

  it("does not create model-supplied HTML or external images", () => {
    const { container } = render(
      <MarkdownMessage content={'<script>window.pwned=true</script>\n\n![tracker](https://evil.example/pixel.gif)'} />,
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText(/已阻止外部图片/)).toBeInTheDocument();
  });

  it("removes unsafe link protocols", () => {
    const { container } = render(<MarkdownMessage content="[bad](javascript:alert(1))" />);
    expect(container.querySelector("a")).toBeNull();
    expect(screen.getByText("bad")).toBeInTheDocument();
  });

  it("copies fenced code", () => {
    render(<MarkdownMessage content={"```js\nconsole.log('ok')\n```"} />);
    fireEvent.click(screen.getByRole("button", { name: /复制/ }));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith("console.log('ok')");
  });
});
