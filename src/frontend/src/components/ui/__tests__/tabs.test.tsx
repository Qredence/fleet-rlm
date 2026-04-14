import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vite-plus/test";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

(
  globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean;
  }
).IS_REACT_ACT_ENVIRONMENT = true;

function requireNode(node: HTMLElement | null, label: string): HTMLElement {
  if (!node) {
    throw new Error(`Expected ${label} ref to resolve to a DOM element`);
  }
  return node;
}

describe("Tabs refs", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("exposes DOM refs through the React 19 ref prop pattern", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    let tabsNode: HTMLElement | null = null;
    let listNode: HTMLElement | null = null;
    let triggerNode: HTMLElement | null = null;
    let contentNode: HTMLElement | null = null;

    act(() => {
      root.render(
        <Tabs
          ref={(node) => {
            tabsNode = node;
          }}
          value="turn"
        >
          <TabsList
            ref={(node) => {
              listNode = node;
            }}
          >
            <TabsTrigger
              ref={(node) => {
                triggerNode = node;
              }}
              value="turn"
            >
              Turn
            </TabsTrigger>
          </TabsList>
          <TabsContent
            ref={(node) => {
              contentNode = node;
            }}
            value="turn"
          >
            Content
          </TabsContent>
        </Tabs>,
      );
    });

    const tabsElement = requireNode(tabsNode, "tabs");
    const listElement = requireNode(listNode, "tabs list");
    const triggerElement = requireNode(triggerNode, "tabs trigger");
    const contentElement = requireNode(contentNode, "tabs content");

    expect(tabsElement).toBeInstanceOf(HTMLElement);
    expect(tabsElement.dataset.slot).toBe("tabs");
    expect(listElement).toBeInstanceOf(HTMLElement);
    expect(listElement.dataset.slot).toBe("tabs-list");
    expect(triggerElement).toBeInstanceOf(HTMLElement);
    expect(triggerElement.dataset.slot).toBe("tabs-trigger");
    expect(contentElement).toBeInstanceOf(HTMLElement);
    expect(contentElement.dataset.slot).toBe("tabs-content");

    act(() => {
      root.unmount();
    });
  });

  it("supports shared default and line variants", () => {
    const defaultHtml = renderToStaticMarkup(
      <Tabs value="turn">
        <TabsList variant="default" className="border border-border-subtle/70">
          <TabsTrigger value="turn">Turn</TabsTrigger>
        </TabsList>
      </Tabs>,
    );

    const lineHtml = renderToStaticMarkup(
      <Tabs value="turn">
        <TabsList variant="line">
          <TabsTrigger value="turn">Turn</TabsTrigger>
        </TabsList>
      </Tabs>,
    );

    expect(defaultHtml).toContain('data-variant="default"');
    expect(defaultHtml).toContain("border-border-subtle/70");
    expect(lineHtml).toContain('data-variant="line"');
    expect(lineHtml).toContain("bg-transparent");
  });
});
