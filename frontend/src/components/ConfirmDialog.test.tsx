import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ConfirmDialog } from "@/components/ConfirmDialog";

function renderDialog(overrides: Partial<React.ComponentProps<typeof ConfirmDialog>> = {}) {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();

  const props = {
    open: true,
    title: 'Delete "Acme Reports"?',
    description: "This permanently deletes the workspace and everything in it.",
    confirmLabel: "Delete workspace",
    onConfirm,
    onCancel,
    ...overrides,
  };

  const view = render(<ConfirmDialog {...props} />);
  return { ...view, onConfirm, onCancel, props };
}

describe("ConfirmDialog", () => {
  it("renders its title, description, and confirm label when open", () => {
    renderDialog();

    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(screen.getByText('Delete "Acme Reports"?')).toBeInTheDocument();
    expect(
      screen.getByText("This permanently deletes the workspace and everything in it.")
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete workspace" })).toBeInTheDocument();
  });

  it("renders nothing when closed", () => {
    renderDialog({ open: false });
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("defaults focus to Cancel, not the destructive action", () => {
    // The safe option should be what Enter triggers on a destructive dialog.
    renderDialog();
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
  });

  it("fires onConfirm when the destructive button is clicked", async () => {
    const user = userEvent.setup();
    const { onConfirm, onCancel } = renderDialog();

    await user.click(screen.getByRole("button", { name: "Delete workspace" }));

    expect(onConfirm).toHaveBeenCalledOnce();
    expect(onCancel).not.toHaveBeenCalled();
  });

  it("fires onCancel when Cancel is clicked", async () => {
    const user = userEvent.setup();
    const { onConfirm, onCancel } = renderDialog();

    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onCancel).toHaveBeenCalledOnce();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("cancels on Escape", async () => {
    const user = userEvent.setup();
    const { onCancel } = renderDialog();

    await user.keyboard("{Escape}");

    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("cancels on a backdrop click but not on a click inside the panel", async () => {
    const user = userEvent.setup();
    const { onCancel } = renderDialog();

    await user.click(screen.getByText('Delete "Acme Reports"?'));
    expect(onCancel).not.toHaveBeenCalled();

    // The backdrop is the dialog's parent; clicking it must not be triggered
    // by clicks bubbling out of the panel.
    const backdrop = screen.getByRole("alertdialog").parentElement as HTMLElement;
    await user.click(backdrop);
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("disables both actions while busy", () => {
    renderDialog({ isBusy: true });

    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Delete workspace/ })).toBeDisabled();
  });

  it("ignores Escape while busy so an in-flight delete is not abandoned", async () => {
    const user = userEvent.setup();
    const { onCancel } = renderDialog({ isBusy: true });

    await user.keyboard("{Escape}");

    expect(onCancel).not.toHaveBeenCalled();
  });

  it("surfaces an error to assistive technology", () => {
    renderDialog({ error: "Could not delete this workspace." });

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Could not delete this workspace.");
  });

  it("restores focus to the trigger when it closes", async () => {
    const user = userEvent.setup();

    function Harness() {
      const [open, setOpen] = React.useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>
            Open
          </button>
          <ConfirmDialog
            open={open}
            title="Delete?"
            description="Gone forever."
            confirmLabel="Delete"
            onConfirm={() => setOpen(false)}
            onCancel={() => setOpen(false)}
          />
        </>
      );
    }

    render(<Harness />);
    const trigger = screen.getByRole("button", { name: "Open" });

    await user.click(trigger);
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();

    await user.click(screen.getByRole("button", { name: "Cancel" }));

    // Keyboard users would otherwise be dropped at the top of the document.
    expect(trigger).toHaveFocus();
  });
});
