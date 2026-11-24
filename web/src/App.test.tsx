import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "./App";
import { APP_NAME } from "./config";

describe("App", () => {
  it("menampilkan nama aplikasi dari konfigurasi", () => {
    render(<App />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(APP_NAME);
  });
});
