/** ECharts, imported piece by piece so the bundle carries only what these charts use. */
import * as echarts from "echarts/core";
import { BarChart, LineChart, PieChart, ScatterChart } from "echarts/charts";
import { GridComponent, LegendComponent, MarkAreaComponent, MarkLineComponent, TitleComponent,
         TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

// Anything not registered here is silently ignored by ECharts: the donut's centre total vanished
// until TitleComponent was added.
echarts.use([ScatterChart, PieChart, BarChart, LineChart, GridComponent, TooltipComponent, LegendComponent,
             MarkLineComponent, MarkAreaComponent, TitleComponent, CanvasRenderer]);

export const COLOUR = { smart: "#2fae87", burned: "#e8674f", heavy: "#4a7fd0", frugal: "#9aa7b1" } as const;
export const LEAGUE_COLOUR: Record<string, string> = {
  "Premier League": "#4a7fd0", "La Liga": "#2fae87", "Serie A": "#e8674f",
  "Bundesliga": "#e0a83c", "Ligue 1": "#8c6fd0",
};

export function init(el: HTMLElement) {
  const chart = echarts.init(el, undefined, { renderer: "canvas" });
  const resize = () => chart.resize();
  window.addEventListener("resize", resize);
  return chart;
}
export type Chart = ReturnType<typeof init>;
