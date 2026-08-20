import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Button } from "./button";

describe("Button", () => {
  it("renders its label and calls onClick", async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Start game</Button>);
    await userEvent.click(screen.getByRole("button", { name: "Start game" }));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("does not call onClick when disabled", async () => {
    const onClick = vi.fn();
    render(
      <Button disabled onClick={onClick}>
        Start game
      </Button>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Start game" }));
    expect(onClick).not.toHaveBeenCalled();
  });
});
