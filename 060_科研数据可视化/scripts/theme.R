# Portable sci-plot foundation: ggplot2/ggprism, bold type, and three-format export.
# This file has no machine-specific font path; all devices retain editable text.

if (!requireNamespace("ggplot2", quietly = TRUE)) {
  stop("R package 'ggplot2' is required")
}
if (!requireNamespace("ggprism", quietly = TRUE)) {
  stop("R package 'ggprism' is required")
}
suppressPackageStartupMessages({
  library(ggplot2)
  library(ggprism)
})

SCIPLOT_CJK_FAMILIES <- c(
  "PingFang SC", "Noto Sans CJK SC", "Source Han Sans SC",
  "Microsoft YaHei", "SimHei", "WenQuanYi Zen Hei", "Arial Unicode MS"
)

sciplot_installed_families <- function() {
  if (!requireNamespace("systemfonts", quietly = TRUE)) return(character())
  tryCatch(unique(systemfonts::system_fonts()[["family"]]), error = function(e) character())
}

sciplot_font_family <- function(cjk = FALSE) {
  families <- sciplot_installed_families()
  candidates <- if (isTRUE(cjk)) SCIPLOT_CJK_FAMILIES else "Arial"
  hit <- candidates[candidates %in% families]
  if (length(hit)) return(hit[[1]])
  if (!isTRUE(cjk) && "Arial" %in% families) return("Arial")
  if (!isTRUE(cjk)) {
    fallback <- c("Helvetica", "Liberation Sans", "DejaVu Sans", "sans")
    hit <- fallback[fallback %in% c(families, "sans")]
    if (length(hit)) return(hit[[1]])
  }
  if (isTRUE(cjk) && "Arial" %in% families) return("Arial")
  "sans"
}

# Explicit breaks and limits keep every mark inside the labelled axis range.
axis_cover <- function(values, n = 4, include_zero = FALSE,
                       lower_mult = 0.02, upper_mult = 0.02) {
  values <- suppressWarnings(as.numeric(values))
  values <- values[is.finite(values)]
  if (!length(values)) values <- 0
  if (include_zero) values <- c(0, values)
  lo <- min(values)
  hi <- max(values)
  if (lo == hi) {
    if (include_zero && lo == 0) {
      lo <- 0
      hi <- 1
    } else {
      delta <- max(abs(lo) * 0.1, .Machine$double.eps)
      if (include_zero) {
        lo <- min(0, lo - delta)
        hi <- max(0, hi + delta)
      } else {
        lo <- lo - delta
        hi <- hi + delta
      }
    }
  }
  breaks <- pretty(c(lo, hi), n = n)
  breaks <- sort(unique(breaks[is.finite(breaks)]))
  if (!length(breaks)) breaks <- c(lo, hi)
  # pretty() 可能返回远多于 n 的刻度（日期/单位取整时翻倍），
  # 硬性隔二抽一直到 ≤ n+1，防止多刻度标签互相重叠。
  max_breaks <- max(2L, n + 1L)
  while (length(breaks) > max_breaks) breaks <- breaks[seq(1L, length(breaks), by = 2L)]
  bottom <- min(c(breaks, lo))
  top <- max(c(breaks, hi))
  if (include_zero) {
    bottom <- min(0, bottom)
    top <- max(0, top)
    if (!any(abs(breaks) < .Machine$double.eps^0.5)) breaks <- sort(unique(c(0, breaks)))
  }
  if (top <= bottom) top <- bottom + 1
  list(
    breaks = breaks,
    bottom = bottom,
    top = top,
    limits = c(bottom, top),
    expand = ggplot2::expansion(mult = c(lower_mult, upper_mult))
  )
}

y_axis_cover <- function(values, n = 4, lower_mult = 0, upper_mult = 0.05) {
  # upper_mult>0：值轴顶端留出 5% 余量，避免最大刻度标签（居中绘制）
  # 在 coord_flip 后被画布右缘裁掉半个字符。
  axis_cover(values, n = n, include_zero = TRUE,
             lower_mult = lower_mult, upper_mult = upper_mult)
}

theme_sciplot <- function(base_size = 16, border = TRUE, family = "Arial") {
  ggprism::theme_prism(base_size = base_size, border = border) +
    ggplot2::theme(
      text = ggplot2::element_text(family = family, face = "bold", colour = "black"),
      axis.text = ggplot2::element_text(family = family, face = "bold", colour = "black"),
      axis.title = ggplot2::element_text(family = family, face = "bold", colour = "black"),
      legend.text = ggplot2::element_text(family = family, face = "bold", colour = "black"),
      legend.title = ggplot2::element_text(family = family, face = "bold", colour = "black"),
      strip.text = ggplot2::element_text(family = family, face = "bold", colour = "black"),
      plot.title = ggplot2::element_text(family = family, face = "bold", colour = "black"),
      plot.subtitle = ggplot2::element_text(family = family, face = "bold", colour = "black"),
      plot.caption = ggplot2::element_text(family = family, face = "bold", colour = "black"),
      plot.tag = ggplot2::element_text(family = family, face = "bold", colour = "black"),
      panel.grid.major = ggplot2::element_blank(),
      panel.grid.minor = ggplot2::element_blank(),
      panel.border = ggplot2::element_rect(color = "black", fill = NA, linewidth = 0.6),
      legend.background = ggplot2::element_rect(color = "black", fill = NA, linewidth = 0.6),
      legend.box.background = ggplot2::element_rect(color = "black", fill = NA, linewidth = 0.6),
      legend.key.spacing.y = grid::unit(12, "points"),
      axis.ticks = ggplot2::element_line(color = "black", linewidth = 0.6)
    )
}

theme_sciplot_offset <- function(base_size = 16, family = "Arial") {
  ggprism::theme_prism(base_size = base_size, border = FALSE) +
    ggplot2::theme(
      text = ggplot2::element_text(family = family, face = "bold", colour = "black"),
      axis.text = ggplot2::element_text(family = family, face = "bold", colour = "black"),
      axis.title = ggplot2::element_text(family = family, face = "bold", colour = "black"),
      legend.text = ggplot2::element_text(family = family, face = "bold", colour = "black"),
      legend.title = ggplot2::element_text(family = family, face = "bold", colour = "black"),
      strip.text = ggplot2::element_text(family = family, face = "bold", colour = "black"),
      panel.grid.major = ggplot2::element_blank(),
      panel.grid.minor = ggplot2::element_blank(),
      panel.border = ggplot2::element_blank(),
      axis.line = ggplot2::element_line(color = "black", linewidth = 0.6),
      legend.background = ggplot2::element_rect(color = "black", fill = NA, linewidth = 0.6),
      legend.box.background = ggplot2::element_rect(color = "black", fill = NA, linewidth = 0.6),
      legend.key.spacing.y = grid::unit(12, "points"),
      axis.ticks = ggplot2::element_line(color = "black", linewidth = 0.6)
    )
}

use_offset_theme <- function(base_size = 16, family = "Arial") {
  ggplot2::theme_set(theme_sciplot_offset(base_size, family))
}

offset_guide <- function() ggprism::guide_prism_offset()

cairo_pdf_sciplot <- function(filename, width, height, family = "Arial", ...) {
  grDevices::cairo_pdf(filename = filename, width = width, height = height,
                       family = family, ...)
}

cairo_pdf_arial <- function(filename, width, height,
                            family = getOption("sciplot.family", "Arial"), ...) {
  cairo_pdf_sciplot(filename, width, height, family = family, ...)
}

svg_sciplot <- function(filename, width, height, ...) {
  if (!requireNamespace("svglite", quietly = TRUE)) {
    stop("R package 'svglite' is required for SVG output")
  }
  svglite::svglite(filename = filename, width = width, height = height,
                   fix_text_size = FALSE, ...)
}

svg_arial <- svg_sciplot

PAL_OKABE <- c(
  "#000000", "#E69F00", "#56B4E9", "#009E73",
  "#F0E442", "#0072B2", "#D55E00", "#CC79A7"
)
PAL_CANDY <- c(
  "#E64B35CC", "#00A087CC", "#3C5488CC", "#F39B7FCC",
  "#8491B4CC", "#91D1C2CC", "#DC0000CC", "#7E6148CC"
)
# Extended qualitative palette for figures with more than eight groups.
PAL_SCI <- c(
  "#0072B2CC", "#E69F00CC", "#009E73CC", "#D55E00CC",
  "#CC79A7CC", "#56B4E9CC", "#F0E442CC", "#000000CC",
  "#332288CC", "#88CCEECC", "#44AA99CC", "#117733CC",
  "#999933CC", "#DDCC77CC", "#661100CC", "#CC6677CC"
)
HEATMAP_GRADIENT <- c("#4575B4CC", "white", "#D73027CC")
COLOR_ROOT <- "#E64B35CC"
COLOR_LEAF <- "#00A087CC"
COLOR_FLOWER <- "#3C5488CC"
COLOR_UP <- "#D73027CC"
COLOR_DOWN <- "#4575B4CC"
COLOR_NS <- "#B0B0B0CC"
COLOR_SPIKE <- "#00A087CC"

# Save to a common basename. Callers can use a temporary basename and atomically
# promote all three files only after every device has succeeded.
save_sciplot <- function(plot, filename, width = 8, height = 5,
                         dpi = 600, family = "Arial",
                         formats = c("pdf", "png", "svg")) {
  base_name <- tools::file_path_sans_ext(filename)
  if ("pdf" %in% formats) {
    pdf_device <- function(filename, width, height, ...) {
      cairo_pdf_sciplot(filename, width, height, family = family, ...)
    }
    ggplot2::ggsave(paste0(base_name, ".pdf"), plot = plot,
                    width = width, height = height, units = "in",
                    device = pdf_device, bg = "white")
  }
  if ("png" %in% formats) {
    ggplot2::ggsave(paste0(base_name, ".png"), plot = plot,
                    width = width, height = height, units = "in",
                    dpi = dpi, device = "png", type = "cairo", bg = "white")
  }
  if ("svg" %in% formats) {
    ggplot2::ggsave(paste0(base_name, ".svg"), plot = plot,
                    width = width, height = height, units = "in",
                    device = svg_sciplot, bg = "white")
  }
  invisible(paste0(base_name, ".", formats))
}

save_sciplot_all <- save_sciplot

ggplot2::theme_set(theme_sciplot())
