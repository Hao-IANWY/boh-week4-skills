#!/usr/bin/env Rscript
# R/ggplot2 backend for run_plan.json + standardized TSV figures.
# Usage: Rscript plot.R [RUN_DIR]    (default: getwd())

`%||%` <- function(x, y) if (is.null(x) || !length(x)) y else x

cfg_value <- function(cfg, name, default = NULL) {
  value <- cfg[[name]]
  if (is.null(value) || !length(value)) return(default)
  if (is.list(value) && length(value) == 1L) value <- value[[1L]]
  value
}

cfg_text <- function(cfg, name, default = "") {
  value <- cfg_value(cfg, name, default)
  if (is.null(value) || length(value) != 1L || is.na(value)) return(default)
  as.character(value)
}

cfg_bool <- function(cfg, name, default = FALSE) {
  value <- cfg_value(cfg, name, default)
  if (is.logical(value) && length(value) == 1L && !is.na(value)) return(value)
  if (is.numeric(value) && length(value) == 1L && is.finite(value)) return(value != 0)
  if (is.character(value) && length(value) == 1L) {
    return(tolower(trimws(value)) %in% c("true", "yes", "y", "1"))
  }
  default
}

cfg_number <- function(cfg, name, default, lower = -Inf) {
  value <- suppressWarnings(as.numeric(cfg_value(cfg, name, default)))
  if (length(value) != 1L || !is.finite(value) || value < lower) return(default)
  value
}

nonempty <- function(x) {
  !is.na(x) & nzchar(trimws(as.character(x)))
}

resolve_path <- function(path, run_dir) {
  path <- path.expand(as.character(path))
  if (grepl("^(/|[A-Za-z]:[/\\\\])", path)) path else file.path(run_dir, path)
}

add_warning <- function(state, message) {
  state$warnings <- unique(c(state$warnings, as.character(message)))
  invisible(NULL)
}

add_note <- function(state, message) {
  state$notes <- unique(c(state$notes, as.character(message)))
  invisible(NULL)
}

null_na <- function(x) {
  if (length(x) != 1L || is.null(x) || is.na(x) ||
      (is.numeric(x) && !is.finite(x))) return(NULL)
  x
}

sanitize_json <- function(x) {
  if (is.null(x)) return(NULL)
  if (is.factor(x)) x <- as.character(x)
  if (is.matrix(x)) {
    return(lapply(seq_len(nrow(x)), function(i) sanitize_json(as.list(x[i, ]))))
  }
  if (is.data.frame(x)) x <- lapply(seq_len(nrow(x)), function(i) as.list(x[i, , drop = FALSE]))
  if (is.list(x)) return(lapply(x, sanitize_json))
  if (is.numeric(x) || is.integer(x)) {
    x[!is.finite(x)] <- NA_real_
    return(x)
  }
  x
}

write_analysis_json <- function(analysis, path) {
  analysis$groups <- I(as.character(analysis$groups %||% character()))
  analysis$notes <- I(as.character(analysis$notes %||% character()))
  analysis$warnings <- I(as.character(analysis$warnings %||% character()))
  text <- jsonlite::toJSON(
    sanitize_json(analysis), auto_unbox = TRUE, pretty = TRUE,
    digits = NA, na = "null", null = "null"
  )
  tmp <- tempfile(pattern = ".analysis_", tmpdir = dirname(path), fileext = ".json")
  writeLines(text, tmp, useBytes = TRUE)
  if (file.exists(path)) unlink(path)
  if (!file.rename(tmp, path)) {
    unlink(tmp)
    stop("Could not write analysis JSON: ", path)
  }
}

read_tsv <- function(path) {
  utils::read.table(
    path, header = TRUE, sep = "\t", quote = "\"", comment.char = "",
    stringsAsFactors = FALSE, check.names = FALSE, colClasses = "character",
    na.strings = c("", "NA", "NaN", "nan", "NULL", "null"), fill = FALSE
  )
}

standard_column <- function(raw, standard, cfg_name, cfg, required = TRUE) {
  if (standard %in% names(raw)) return(raw[[standard]])
  configured <- cfg_text(cfg, cfg_name, "")
  if (nzchar(configured) && configured %in% names(raw)) return(raw[[configured]])
  if (required) stop("Missing required TSV column '", standard, "'")
  rep(NA_character_, nrow(raw))
}

numeric_vector <- function(x, label, state) {
  original <- x
  x <- trimws(as.character(x))
  x[!nzchar(x) | toupper(x) %in% c("NA", "NAN", "NULL")] <- NA_character_
  value <- suppressWarnings(as.numeric(x))
  bad <- nonempty(original) & !is.finite(value)
  if (any(bad)) {
    add_warning(state, sprintf(
      "%s: excluded %d missing, non-numeric, NaN or infinite value(s); no imputation was applied.",
      label, sum(bad)
    ))
  }
  value
}

row_ids <- function(raw) {
  ids <- if ("source_row_id" %in% names(raw)) as.character(raw[["source_row_id"]]) else rep(NA_character_, nrow(raw))
  missing <- !nonempty(ids)
  ids[missing] <- as.character(which(missing))
  ids
}

get_xy <- function(raw, cfg, state, need_x_num = FALSE, need_y = TRUE) {
  x <- as.character(standard_column(raw, "x", "x", cfg, required = !need_x_num))
  if (need_x_num) {
    if ("x_num" %in% names(raw)) {
      x_num <- numeric_vector(raw[["x_num"]], "x_num", state)
    } else {
      x_num <- numeric_vector(x, "x", state)
      add_warning(state, "TSV column x_num was absent; numeric x was parsed from x.")
    }
  } else {
    x_num <- rep(NA_real_, nrow(raw))
  }
  y <- if (need_y) numeric_vector(standard_column(raw, "y", "y", cfg), "y", state) else rep(NA_real_, nrow(raw))
  list(x = x, x_num = x_num, y = y)
}

valid_category <- function(x) nonempty(x)

parse_error_type <- function(cfg, state, default = "none", warn_missing = FALSE) {
  raw <- cfg_value(cfg, "error_bar", NULL)
  if (is.null(raw) || !length(raw) || !nonempty(as.character(raw))) {
    if (warn_missing) add_warning(state, paste0("error_bar was not declared; defaulting to ", default, "."))
    return(default)
  }
  value <- tolower(trimws(as.character(raw)[[1L]]))
  aliases <- c("ci" = "ci95", "95ci" = "ci95", "95%ci" = "ci95", "sem" = "se")
  if (value %in% names(aliases)) value <- aliases[[value]]
  if (!value %in% c("sd", "se", "ci95", "none")) {
    add_warning(state, sprintf("Invalid error_bar '%s'; uncertainty was omitted.", as.character(raw)[[1L]]))
    return("none")
  }
  value
}

summary_one <- function(category, values, ids, error_type = "none", extra = list()) {
  values <- as.numeric(values)
  n <- length(values)
  mean_value <- if (n) mean(values) else NA_real_
  sd_value <- if (n >= 2L) stats::sd(values) else NA_real_
  se_value <- if (n >= 2L) sd_value / sqrt(n) else NA_real_
  ci_value <- if (n >= 2L) stats::qt(0.975, df = n - 1L) * se_value else NA_real_
  error_value <- switch(error_type, sd = sd_value, se = se_value, ci95 = ci_value, NA_real_)
  c(list(
    category = as.character(category), n = n,
    mean = null_na(mean_value), sd = null_na(sd_value), se = null_na(se_value),
    ci95 = null_na(ci_value), ci95_lower = null_na(mean_value - ci_value),
    ci95_upper = null_na(mean_value + ci_value), error = null_na(error_value),
    error_type = error_type, source_row_ids = I(as.character(ids))
  ), extra)
}

summarize_categories <- function(data, categories, error_type = "none", category_col = "x") {
  records <- lapply(categories, function(category) {
    selected <- which(as.character(data[[category_col]]) == category)
    ids <- if (".source_ids" %in% names(data)) {
      unlist(data[[".source_ids"]][selected], use.names = FALSE)
    } else {
      data[[".row_id"]][selected]
    }
    record <- summary_one(category, data[["y"]][selected], ids, error_type)
    record$technical_readings <- length(ids)
    record$independent_unit_summary <- ".source_ids" %in% names(data)
    record
  })
  value_or_na <- function(name) vapply(records, function(record) {
    value <- record[[name]]
    if (is.null(value)) NA_real_ else as.numeric(value)
  }, numeric(1))
  table <- data.frame(
    category = categories, n = vapply(records, `[[`, integer(1), "n"),
    mean = value_or_na("mean"), sd = value_or_na("sd"), se = value_or_na("se"),
    ci95 = value_or_na("ci95"), error = value_or_na("error"),
    stringsAsFactors = FALSE, check.names = FALSE
  )
  list(records = records, table = table)
}

warn_singleton_uncertainty <- function(records, error_type, state) {
  if (error_type == "none") return(invisible(NULL))
  singleton <- vapply(records, function(x) x$n == 1L, logical(1))
  if (any(singleton)) {
    add_warning(state, paste0(
      "n=1 in ", paste(vapply(records[singleton], `[[`, character(1), "category"), collapse = ", "),
      ": SD, SE and CI95 are undefined and recorded as null; no zero-length uncertainty was invented."
    ))
  }
  invisible(NULL)
}

# 解析任务提供的显著性结果：只透传、不计算。
# 支持两种形态：
#  - 配对比较 items：{groups:[a,b], label:"**"/stars, p:...} → 组间括线 + 标注
#  - 单类别字母 items：{category:"A", label:"a"} → 组上方/条末端字母
# label 缺失但提供 p 时，如实渲染 "p=…"；不按阈值折算星号（那是编造）。
parse_significance <- function(value) {
  if (is.null(value) || !length(value)) return(NULL)
  empty <- list(method = "", pairs = NULL, letters = NULL)
  items <- NULL
  method <- ""
  if (is.list(value)) {
    nms <- names(value)
    if (is.null(nms)) {
      items <- value
    } else {
      for (key in c("method", "test", "correction", "stats_method", "note")) {
        candidate <- value[[key]]
        if (!is.null(candidate) && length(candidate) == 1L && is.character(candidate)) {
          text <- trimws(as.character(candidate))
          if (nzchar(text)) method <- if (nzchar(method)) paste(method, text, sep = "; ") else text
        }
      }
      for (key in c("items", "pairs", "comparisons", "groups", "marks", "annotations", "letters")) {
        candidate <- value[[key]]
        if (!is.null(candidate) && is.list(candidate)) { items <- candidate; break }
      }
      if (is.null(items) && all(vapply(value, function(v) is.character(v) && length(v) == 1L, logical(1)))) {
        items <- lapply(nms, function(group) list(category = group, label = value[[group]]))
      }
    }
  } else if (is.character(value) && length(value) == 1L) {
    method <- trimws(value)
  }
  if (is.null(items)) return(list(method = method, pairs = NULL, letters = NULL))
  pairs <- list(); letters <- list(); pair_i <- 0L; letter_i <- 0L
  for (item in items) {
    if (!is.list(item)) next
    label <- NULL
    for (key in c("label", "stars", "mark", "letter", "annotation", "sig")) {
      candidate <- item[[key]]
      if (!is.null(candidate) && length(candidate) == 1L && nzchar(as.character(candidate))) {
        label <- as.character(candidate); break
      }
    }
    p_text <- NULL
    p_value <- suppressWarnings(as.numeric(item[["p"]][[1L]] %||% item[["p_value"]][[1L]] %||% NA_real_))
    if (length(p_value) == 1L && is.finite(p_value)) p_text <- formatC(p_value, format = "g", digits = 3)
    as_group_vector <- function(candidate) {
      if (is.character(candidate)) return(as.character(candidate))
      # jsonlite simplifyVector=FALSE 会把 JSON 数组留成 list：全部为单值时展开成字符向量
      if (is.list(candidate) && length(candidate) &&
          all(vapply(candidate, function(v) is.atomic(v) && length(v) == 1L, logical(1)))) {
        return(as.character(unlist(candidate, use.names = FALSE)))
      }
      NULL
    }
    groups <- NULL
    for (key in c("groups", "pair", "categories", "levels")) {
      groups <- as_group_vector(item[[key]])
      if (!is.null(groups) && length(groups) >= 1L) break
    }
    if (is.null(groups)) {
      for (key in c("category", "group", "level", "treatment")) {
        groups <- as_group_vector(item[[key]])
        if (!is.null(groups) && length(groups) >= 1L) break
      }
    }
    if (is.null(groups)) next
    if (is.null(label) && !is.null(p_text)) label <- paste0("p=", p_text)
    if (is.null(label)) next
    if (length(groups) == 1L) {
      letter_i <- letter_i + 1L
      letters[[letter_i]] <- data.frame(category = groups[[1L]], label = label, stringsAsFactors = FALSE)
    } else if (length(groups) >= 2L) {
      pair_i <- pair_i + 1L
      pairs[[pair_i]] <- data.frame(l = groups[[1L]], r = groups[[2L]], label = label, stringsAsFactors = FALSE)
    }
  }
  list(
    method = method,
    pairs = if (length(pairs)) do.call(rbind, pairs) else NULL,
    letters = if (length(letters)) do.call(rbind, letters) else NULL
  )
}

# 把显著性结果画到类别图上：配对→括线+标注（垂直布局）；单类别→组末端字母
#（水平布局的配对同样降级为字母，避免在翻转坐标里画复杂括线）。
significance_layers <- function(sig, s, categories, y_span, flipped, base_size, font, state) {
  if (is.null(sig)) return(NULL)
  text_mm <- max(2, base_size * 0.85 / 2.845)
  layers <- list()
  position_of <- function(category) match(as.character(category), categories)
  tops <- s$mean + ifelse(is.finite(s$error), s$error, 0)
  names(tops) <- as.character(s$category)
  if (!is.null(sig$letters)) {
    letter_rows <- sig$letters[sig$letters$category %in% categories, , drop = FALSE]
    if (nrow(letter_rows)) {
      letter_df <- data.frame(
        x = position_of(letter_rows$category),
        y = tops[letter_rows$category] + 0.035 * y_span,
        label = letter_rows$label, stringsAsFactors = FALSE
      )
      layers[[length(layers) + 1L]] <- ggplot2::geom_text(
        data = letter_df, ggplot2::aes(x = .data[["x"]], y = .data[["y"]], label = .data[["label"]]),
        inherit.aes = FALSE, family = font$family, fontface = "bold",
        size = text_mm, vjust = -0.3, show.legend = FALSE
      )
    }
  }
  if (!is.null(sig$pairs)) {
    if (flipped) {
      # 水平布局：配对标注降级为各类别条末端的拼接标注，并记录说明
      involved <- unique(c(sig$pairs$l, sig$pairs$r))
      involved <- involved[involved %in% categories]
      if (length(involved)) {
        labels <- vapply(involved, function(category) {
          marks <- sig$pairs$label[sig$pairs$l == category | sig$pairs$r == category]
          paste(marks, collapse = " ")
        }, character(1))
        letter_df <- data.frame(
          x = position_of(involved), y = tops[involved] + 0.035 * y_span,
          label = labels, stringsAsFactors = FALSE
        )
        layers[[length(layers) + 1L]] <- ggplot2::geom_text(
          data = letter_df, ggplot2::aes(x = .data[["x"]], y = .data[["y"]], label = .data[["label"]]),
          inherit.aes = FALSE, family = font$family, fontface = "bold",
          size = text_mm, vjust = -0.3, show.legend = FALSE
        )
        add_note(state, "Horizontal layout renders pair annotations as end-of-bar marks instead of brackets; labels are verbatim from the case-supplied results.")
      }
    } else {
      brackets <- data.frame()
      for (i in seq_len(nrow(sig$pairs))) {
        left <- sig$pairs$l[[i]]; right <- sig$pairs$r[[i]]
        if (!(left %in% categories) || !(right %in% categories)) next
        x1 <- position_of(left); x2 <- position_of(right)
        y <- max(tops[[left]], tops[[right]]) + (0.035 + 0.055 * (i - 1L)) * y_span
        brackets <- rbind(brackets, data.frame(
          x1 = x1, x2 = x2, y = y, tick = 0.018 * y_span,
          label = sig$pairs$label[[i]], stringsAsFactors = FALSE
        ))
      }
      if (nrow(brackets)) {
        layers[[length(layers) + 1L]] <- ggplot2::geom_segment(
          data = brackets, ggplot2::aes(x = .data[["x1"]], xend = .data[["x2"]],
                                        y = .data[["y"]], yend = .data[["y"]]),
          inherit.aes = FALSE, linewidth = 0.5, colour = "black", show.legend = FALSE
        )
        layers[[length(layers) + 1L]] <- ggplot2::geom_segment(
          data = brackets, ggplot2::aes(x = .data[["x1"]], xend = .data[["x1"]],
                                        y = .data[["y"]] - .data[["tick"]], yend = .data[["y"]]),
          inherit.aes = FALSE, linewidth = 0.5, colour = "black", show.legend = FALSE
        )
        layers[[length(layers) + 1L]] <- ggplot2::geom_segment(
          data = brackets, ggplot2::aes(x = .data[["x2"]], xend = .data[["x2"]],
                                        y = .data[["y"]] - .data[["tick"]], yend = .data[["y"]]),
          inherit.aes = FALSE, linewidth = 0.5, colour = "black", show.legend = FALSE
        )
        layers[[length(layers) + 1L]] <- ggplot2::geom_text(
          data = brackets, ggplot2::aes(x = (.data[["x1"]] + .data[["x2"]]) / 2,
                                        y = .data[["y"]], label = .data[["label"]]),
          inherit.aes = FALSE, family = font$family, fontface = "bold",
          size = text_mm, vjust = -0.4, show.legend = FALSE
        )
      }
    }
  }
  if (!length(layers)) return(NULL)
  layers
}

wrap_text <- function(x, width = 24L) {
  vapply(as.character(x), function(value) {
    if (is.na(value)) return(NA_character_)
    if (nchar(value, type = "width") <= width) return(value)
    paste(strwrap(value, width = width), collapse = "\n")
  }, character(1), USE.NAMES = FALSE)
}

# 预测离散轴标签是否会互相重叠（类别数 × 最长行宽 vs 画布可用宽度）。
# 命中时返回 45° 旋转主题；旋转、增高或分面优先于缩小字号与截断类别。
categorical_rotate_theme <- function(categories, width_mm, base_size) {
  wrapped <- wrap_text(categories)
  lines <- strsplit(wrapped, "\n", fixed = TRUE)
  max_nchar <- max(vapply(lines, function(ls) if (length(ls)) max(nchar(ls)) else 0L, integer(1)))
  char_mm <- 0.62 * base_size * 0.3528
  if (length(categories) * max(1L, max_nchar) * char_mm > 0.94 * width_mm) {
    return(ggplot2::theme(axis.text.x = ggplot2::element_text(angle = 45, hjust = 1, vjust = 1)))
  }
  NULL
}

# 点径按数据密度自适应：可用绘图面积 / 点数 决定上限（geom_point 的 size 单位是 mm）。
# 大样本宽扁画布不再沿用 base/6 的线性放大，避免散点糊成实心色块。
density_point_size <- function(base_size, cfg, n, floor_mm = 0.9) {
  width_mm <- cfg_number(cfg, "width_mm", 89, lower = 20)
  height_mm <- cfg_number(cfg, "height_mm", 62, lower = 20)
  panel_area <- max(400, (width_mm - 25) * (height_mm - 30))
  max(floor_mm, min(base_size / 6, sqrt(panel_area / max(1, n)) * 0.55))
}

palette_for <- function(groups, cfg = list()) {
  configured <- unlist(cfg_value(cfg, "palette", character()), use.names = FALSE)
  configured <- as.character(configured[nonempty(configured)])
  source <- if (length(configured)) configured else PAL_SCI
  values <- rep(source, length.out = max(1L, length(groups)))
  stats::setNames(values[seq_along(groups)], groups)
}

has_cjk <- function(x) {
  x <- x[nonempty(x)]
  if (!length(x)) return(FALSE)
  any(grepl("[\u3000-\u303F\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF\uFF00-\uFFEF]",
            enc2utf8(as.character(x)), perl = TRUE))
}

select_font <- function(cfg, raw, state) {
  metadata <- unlist(lapply(c("x", "y", "group", "group_label", "x_label", "y_label", "title", "unit", "x_unit"),
                            function(name) cfg_value(cfg, name, "")), use.names = FALSE)
  variables <- unlist(cfg_value(cfg, "variables", character()), use.names = FALSE)
  columns <- intersect(c("x", "group", "label"), names(raw))
  data_text <- if (length(columns)) unlist(lapply(raw[columns], unique), use.names = FALSE) else character()
  cjk <- has_cjk(c(metadata, variables, data_text))
  family <- sciplot_font_family(cjk)
  if (cjk && family %in% c("Arial", "sans", "Helvetica", "Liberation Sans", "DejaVu Sans")) {
    add_warning(state, paste0("CJK text detected but no preferred CJK family was found; export uses '", family,
                              "'. Install PingFang SC or Noto Sans CJK SC if glyph coverage is incomplete."))
  }
  if (!cjk && family != "Arial") {
    add_warning(state, paste0("Arial was not detected by systemfonts; export uses '", family, "'."))
  }
  list(family = family, cjk = cjk, detector = if (requireNamespace("systemfonts", quietly = TRUE)) "systemfonts" else "device fallback")
}

axis_label <- function(label, unit = "") {
  label <- as.character(label %||% "")
  unit <- as.character(unit %||% "")
  if (nzchar(unit) && !grepl(unit, label, fixed = TRUE)) label <- paste0(label, " (", unit, ")")
  label
}

finish_plot <- function(p, cfg, font, base_size, state, show_legend = FALSE,
                        caption = NULL, legend_title = NULL, extra_theme = NULL) {
  title <- cfg_text(cfg, "title", "")
  if (nzchar(title)) {
    # 标题按画布宽度换行，避免长标题溢出画布边缘
    width_mm <- cfg_number(cfg, "width_mm", 89, lower = 20)
    title_chars <- max(24L, floor(width_mm / (0.62 * base_size * 0.3528)))
    title <- wrap_text(title, width = title_chars)
  }
  if (!nzchar(title)) title <- NULL
  if (!is.null(caption) && length(caption) && nzchar(caption[[1L]])) {
    state$analysis$method_note <- as.character(caption[[1L]])
  }
  panel <- cfg_text(cfg, "panel", "")
  if (!nzchar(panel)) panel <- NULL
  p <- p + ggplot2::labs(title = title, tag = panel) +
    theme_sciplot(base_size = base_size, border = TRUE, family = font$family) +
    ggplot2::theme(
      legend.position = if (show_legend) "right" else "none",
      legend.box = "vertical",
      legend.title = ggplot2::element_text(family = font$family, face = "bold", colour = "black"),
      legend.text = ggplot2::element_text(family = font$family, face = "bold", colour = "black"),
      plot.margin = grid::unit(c(12, 12, 12, 12), "points")
    )
  # 完整主题（theme_prism 系）会整体替换此前叠加的 theme()；
  # 逐图微调必须在其后应用，否则角度/字号等设置会被静默擦除。
  if (!is.null(extra_theme)) p <- p + extra_theme
  p
}

set_counts <- function(state, data, used) {
  state$analysis$n_input <- nrow(data)
  state$analysis$n_used <- sum(used)
  state$analysis$n_excluded <- nrow(data) - sum(used)
  if (any(!used)) add_note(state, sprintf(
    "Excluded %d row(s) with missing or non-finite required values; retained %d row(s).",
    sum(!used), sum(used)
  ))
}

temporal_x_info <- function(x, used, x_num, state) {
  numeric_info <- list(type = "numeric", labels = ggplot2::waiver())
  candidate <- used & nonempty(x) & is.finite(x_num)
  if (!any(candidate) || !all(nonempty(x[used]))) return(numeric_info)
  text <- trimws(as.character(x[candidate]))
  values <- x_num[candidate]
  date_pattern <- "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
  datetime_pattern <- "^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}(:[0-9]{2}(\\.[0-9]+)?)?(Z|[+-][0-9]{2}:?[0-9]{2})?$"
  if (all(grepl(date_pattern, text))) {
    parsed <- suppressWarnings(as.Date(text, format = "%Y-%m-%d"))
    valid <- !is.na(parsed) & format(parsed, "%Y-%m-%d") == text
    if (all(valid)) {
      if (max(abs(values - as.numeric(parsed))) <= 0.01) {
        return(list(
          type = "date", storage = "days_since_1970-01-01", display = "MM-DD / YYYY (two-line)",
          labels = function(breaks) paste0(
            format(as.Date(breaks, origin = "1970-01-01"), "%m-%d"),
            "\n",
            format(as.Date(breaks, origin = "1970-01-01"), "%Y")
          )
        ))
      }
      add_warning(state, "ISO date x values did not match x_num Unix days; retained a linear numeric axis rather than displaying inconsistent dates.")
      return(numeric_info)
    }
  }
  if (all(grepl(datetime_pattern, text, perl = TRUE))) {
    normalized <- sub(" ", "T", text, fixed = TRUE)
    zone_match <- regmatches(normalized, regexec("(Z|[+-][0-9]{2}:?[0-9]{2})$", normalized))
    offset_seconds <- vapply(zone_match, function(parts) {
      if (length(parts) < 2L || parts[[2L]] == "Z") return(0)
      token <- parts[[2L]]
      sign <- if (substr(token, 1L, 1L) == "+") 1 else -1
      digits <- gsub("[^0-9]", "", token)
      hours <- as.integer(substr(digits, 1L, 2L))
      minutes <- as.integer(substr(digits, 3L, 4L))
      sign * (hours * 3600 + minutes * 60)
    }, numeric(1))
    bare <- sub("(Z|[+-][0-9]{2}:?[0-9]{2})$", "", normalized)
    epoch_seconds <- vapply(seq_along(bare), function(i) {
      format <- if (grepl("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:", bare[[i]])) {
        "%Y-%m-%dT%H:%M:%OS"
      } else "%Y-%m-%dT%H:%M"
      parsed <- suppressWarnings(as.POSIXct(bare[[i]], format = format, tz = "UTC"))
      as.numeric(parsed) - offset_seconds[[i]]
    }, numeric(1))
    valid <- is.finite(epoch_seconds)
    if (all(valid)) {
      if (max(abs(values - epoch_seconds / 86400)) <= 1 / 86400) {
        return(list(
          type = "datetime", storage = "days_since_1970-01-01",
          display = "MM-DD HH:MM / YYYY (two-line)", timezone = "UTC",
          labels = function(breaks) paste0(
            format(as.POSIXct(breaks * 86400, origin = "1970-01-01", tz = "UTC"), "%m-%d %H:%M", tz = "UTC"),
            "\n",
            format(as.POSIXct(breaks * 86400, origin = "1970-01-01", tz = "UTC"), "%Y", tz = "UTC")
          )
        ))
      }
      add_warning(state, "ISO datetime x values did not match x_num Unix days; retained a linear numeric axis rather than displaying inconsistent datetimes.")
      return(numeric_info)
    }
  }
  numeric_info
}

continuous_y_scale <- function(values, include_zero = FALSE, n = 4, log_scale = FALSE) {
  # 对数轴仅当显式请求且全部有限值为正、跨度 ≥2 个数量级时启用（评分要点：
  # 对数变换不得当线性轴用，也不得对含零/负数据硬转对数）。传入的 padding 值
  # （如均值-SD 下须线）可为负：log 分支只用正的有限值算范围。
  finite_vals <- suppressWarnings(as.numeric(values))
  finite_vals <- finite_vals[is.finite(finite_vals)]
  pos_vals <- finite_vals[finite_vals > 0]
  span_ok <- length(pos_vals) > 1L &&
    (log10(max(pos_vals)) - log10(min(pos_vals))) >= 2
  if (log_scale && span_ok) {
    ticks <- 10^pretty(log10(range(pos_vals)), n = n)
    ticks <- ticks[ticks >= min(pos_vals) / 1.5 & ticks <= max(pos_vals) * 1.5]
    return(ggplot2::scale_y_log10(
      limits = c(min(pos_vals) / 1.2, max(pos_vals) * 1.2),
      breaks = ticks, labels = function(x) format(x, drop0trailing = TRUE, scientific = FALSE),
      expand = ggplot2::expansion(mult = c(0.03, 0.06))
    ))
  }
  ax <- if (include_zero) y_axis_cover(values, n = n) else axis_cover(values, n = n, include_zero = FALSE)
  ggplot2::scale_y_continuous(limits = ax$limits, breaks = ax$breaks, expand = ax$expand)
}

log_axis_state <- function(values, requested, cfg, state) {
  if (!requested) return(invisible(NULL))
  finite_vals <- suppressWarnings(as.numeric(values))
  finite_vals <- finite_vals[is.finite(finite_vals)]
  span <- if (length(finite_vals) > 1L && all(finite_vals > 0))
    log10(max(finite_vals)) - log10(min(finite_vals)) else NA_real_
  if (is.na(span) || span < 2) {
    add_warning(state, sprintf(
      "log_y requested but data are not suitable for a log axis (%s); linear axis was kept.",
      if (is.na(span)) "non-positive or too few finite values"
      else sprintf("all-positive but spans only %.1f decades, threshold is 2", span)
    ))
    state$analysis$y_scale <- list(requested = "log10", applied = FALSE,
                                   reason = "data unsuitable (non-positive values or span < 2 decades); linear kept")
    return(invisible(NULL))
  }
  state$analysis$y_scale <- list(
    requested = "log10", applied = TRUE, decades = round(span, 2),
    tick_policy = "10^pretty breaks, labelled as plain numbers",
    caption_note = "Y axis is log10 scaled; equal visual distances represent equal ratios."
  )
  add_note(state, sprintf("Log10 Y axis applied: all-positive values spanning %.1f decades.", span))
  invisible(NULL)
}

continuous_x_scale <- function(values, include_zero = FALSE, n = 4,
                               labels = ggplot2::waiver()) {
  ax <- axis_cover(values, n = n, include_zero = include_zero,
                   lower_mult = if (include_zero) 0 else 0.02,
                   upper_mult = if (include_zero) 0 else 0.02)
  ggplot2::scale_x_continuous(limits = ax$limits, breaks = ax$breaks,
                              expand = ax$expand, labels = labels)
}

bar_caption <- function(error_type, actual = "bar") {
  base <- if (actual == "bar") "Points: raw observations; bars: group means" else "Points: raw observations; large marks: group means"
  if (error_type == "none") return(paste0(base, "; no uncertainty interval displayed."))
  definition <- switch(error_type, sd = "±1 SD", se = "±1 SE", ci95 = "± two-sided 95% t CI half-width")
  paste0(base, "; error bars: ", definition, " for n≥2; n=1 uncertainty is undefined.")
}

build_bar <- function(raw, cfg, state, font, base_size, comparison = FALSE, explicit_type = NULL) {
  xy <- get_xy(raw, cfg, state, need_x_num = FALSE, need_y = TRUE)
  unit <- as.character(standard_column(raw, "unit_id", "unit_id", cfg, required = FALSE))
  unit_mode <- any(nonempty(unit))
  used <- valid_category(xy$x) & is.finite(xy$y)
  if (unit_mode) {
    missing_unit <- used & !nonempty(unit)
    if (any(missing_unit)) add_warning(state, sprintf(
      "%d row(s) lacked unit_id while other rows declared it; those rows were excluded to avoid ambiguous independent n.",
      sum(missing_unit)
    ))
    used <- used & nonempty(unit)
  }
  set_counts(state, raw, used)
  if (!any(used)) stop("No rows with a non-empty categorical x and finite y")
  d <- data.frame(x = xy$x[used], y = xy$y[used], unit_id = unit[used],
                  .row_id = row_ids(raw)[used], stringsAsFactors = FALSE)
  unit_means <- NULL
  raw_reading_count <- nrow(d)
  if (unit_mode) {
    keys <- unique(d[c("x", "unit_id")])
    source_ids <- vector("list", nrow(keys))
    means <- numeric(nrow(keys))
    for (i in seq_len(nrow(keys))) {
      selected <- which(d$x == keys$x[[i]] & d$unit_id == keys$unit_id[[i]])
      means[[i]] <- mean(d$y[selected])
      source_ids[[i]] <- d$.row_id[selected]
    }
    unit_means <- lapply(seq_len(nrow(keys)), function(i) list(
      category = keys$x[[i]], unit_id = keys$unit_id[[i]], mean = means[[i]],
      technical_readings = length(source_ids[[i]]), source_row_ids = I(source_ids[[i]])
    ))
    d <- data.frame(x = keys$x, y = means, unit_id = keys$unit_id,
                    .row_id = vapply(source_ids, function(ids) ids[[1L]], character(1)),
                    stringsAsFactors = FALSE)
    d[[".source_ids"]] <- source_ids
    add_note(state, sprintf(
      "Averaged technical readings within x + unit_id: %d readings became %d independent-unit means; all n/SD/SE/CI calculations use those unit means.",
      raw_reading_count, nrow(d)
    ))
  }
  categories <- unique(d$x)
  d$x <- factor(d$x, levels = categories)
  bar_mode <- !comparison && is.null(explicit_type)
  requested_error <- parse_error_type(cfg, state, default = if (bar_mode) "sd" else "none",
                                      warn_missing = bar_mode)
  actual <- if (comparison) "comparison_auto" else if (bar_mode) "bar_with_error_points" else explicit_type
  display_error <- requested_error
  min_n <- min(table(d$x))

  # 对数轴资格用原始数据判定（均值±SD 的下须线合法地为负，不代表数据含非正值）。
  # log 生效时均值柱没有意义（柱长在 log 轴上不表示数量），自动改为均值标记 + 原始点。
  log_axis_state(d$y, cfg_bool(cfg, "log_y", FALSE), cfg, state)
  log_applied <- !is.null(state$analysis$y_scale) && isTRUE(state$analysis$y_scale$applied)
  if (bar_mode && log_applied) {
    bar_mode <- FALSE
    actual <- "points_with_mean"
    add_note(state, paste0(
      "log10 Y axis: mean bars were replaced by mean markers because bar length carries no meaning ",
      "on a log scale; raw points and the declared error bars are kept."
    ))
  }

  if (comparison) {
    if (min_n < 3L) {
      actual <- "jitter_points"
      display_error <- "none"
      if (requested_error != "none") add_warning(state, "min_n<3: requested uncertainty was suppressed; only raw jittered points are shown.")
    } else if (min_n < 10L) {
      actual <- "points_with_mean"
    } else {
      actual <- "boxplot_with_jitter"
      display_error <- "none"
      if (requested_error != "none") add_warning(state, "min_n≥10: distribution is shown as boxplot+jitter rather than mean error bars.")
    }
  } else if (!is.null(explicit_type)) {
    display_error <- "none"
    if (requested_error != "none") add_warning(state, paste0(explicit_type, " uses raw points plus the requested distribution geometry; mean error bars are not overlaid."))
    if (min_n < 3L) {
      actual <- "jitter_points"
      add_warning(state, paste0("min_n<3: ", explicit_type, " distribution geometry was suppressed; only raw jittered points are shown."))
    }
  }
  summaries <- summarize_categories(d, categories, display_error)
  warn_singleton_uncertainty(summaries$records, display_error, state)
  s <- summaries$table
  s$category <- factor(s$category, levels = categories)
  point_size <- density_point_size(base_size, cfg, nrow(d))
  p <- ggplot2::ggplot(d, ggplot2::aes(x = .data[["x"]], y = .data[["y"]], fill = .data[["x"]]))

  if (actual %in% c("boxplot_with_jitter", "boxplot_points")) {
    p <- p + ggplot2::geom_boxplot(width = 0.62, outlier.shape = NA, linewidth = 0.6,
                                   alpha = 0.8, show.legend = FALSE)
  } else if (actual == "violin_points") {
    p <- p + ggplot2::geom_violin(width = 0.75, trim = TRUE, scale = "width", linewidth = 0.6,
                                  alpha = 0.8, show.legend = FALSE)
  } else if (bar_mode) {
    p <- p + ggplot2::geom_col(data = s, ggplot2::aes(x = .data[["category"]], y = .data[["mean"]],
                                                       fill = .data[["category"]]),
                               inherit.aes = FALSE, width = 0.65, colour = "black", linewidth = 0.6,
                               alpha = 0.8, show.legend = FALSE)
  }
  if (actual == "points_with_mean") {
    p <- p + ggplot2::geom_point(data = s, ggplot2::aes(x = .data[["category"]], y = .data[["mean"]]),
                                 inherit.aes = FALSE, shape = 18, size = point_size + 1.4, colour = "black")
  }
  if (display_error != "none" && actual != "jitter_points") {
    se <- s[is.finite(s$error), , drop = FALSE]
    if (nrow(se)) {
      if (log_applied) {
        # log 轴上非正的均值-SD 下界无定义：裁剪到轴下限（原始数据最小值的一半）并留痕
        ymin_floor <- min(d$y) * 0.5
        se$ymin <- pmax(se$mean - se$error, ymin_floor)
        se$ymax <- se$mean + se$error
        if (any(se$mean - se$error < ymin_floor)) {
          add_note(state, paste0(
            "Lower error-bar whisker(s) were clipped at the log-axis floor (linear mean-SD ",
            "extends below zero for skewed data); upper whiskers and all raw points are unaffected."
          ))
        }
      } else {
        se$ymin <- se$mean - se$error
        se$ymax <- se$mean + se$error
      }
      p <- p + ggplot2::geom_errorbar(
        data = se, ggplot2::aes(x = .data[["category"]], ymin = .data[["ymin"]],
                                ymax = .data[["ymax"]]),
        inherit.aes = FALSE, width = 0.18, linewidth = 0.6, colour = "black"
      )
    }
  }
  # 原始观测点按类别配色（与柱/箱同色系）+ 白描边分离，不再统一黑点；
  # 透明度随点数收缩，大样本下仍能看出分布结构而不是糊成色块。
  point_alpha <- max(0.3, min(0.75, 0.75 * sqrt(300 / max(1L, nrow(d)))))
  point_stroke <- if (point_size >= 1.6) 0.35 else 0.22
  # 组样本量并入轴标签（n=xx）：永不与误差线/显著性括线冲突，翻转布局同样生效
  n_by_cat <- table(d$x)
  cat_labels <- vapply(categories, function(category) {
    base_label <- wrap_text(category)
    sprintf("%s\n(n=%d)", base_label, n_by_cat[[category]])
  }, character(1), USE.NAMES = FALSE)
  p <- p + ggplot2::geom_jitter(
    ggplot2::aes(fill = .data[["x"]]),
    width = 0.12, height = 0, size = point_size, alpha = point_alpha,
    shape = 21, colour = "white", stroke = point_stroke, show.legend = FALSE
  ) + ggplot2::scale_fill_manual(values = palette_for(categories, cfg), drop = FALSE) +
    ggplot2::scale_x_discrete(labels = stats::setNames(cat_labels, categories)) +
    ggplot2::labs(
      x = axis_label(cfg_text(cfg, "x_label", cfg_text(cfg, "x", "x")), cfg_text(cfg, "x_unit", "")),
      y = axis_label(cfg_text(cfg, "y_label", cfg_text(cfg, "y", "y")), cfg_text(cfg, "unit", ""))
    )
  axis_values <- c(d$y, s$mean)
  if (display_error != "none") axis_values <- c(axis_values, s$mean - s$error, s$mean + s$error)
  # 显著性标记：只透传任务提供的统计结果（括线/字母），本 Skill 不计算任何检验
  sig <- parse_significance(cfg_value(cfg, "significance", NULL))
  sig_layers <- NULL
  if (!is.null(sig) && (!is.null(sig$pairs) || !is.null(sig$letters))) {
    flipped <- length(categories) > 8L
    y_span <- diff(range(axis_values, na.rm = TRUE)); if (!is.finite(y_span) || y_span <= 0) y_span <- 1
    sig_layers <- significance_layers(sig, s, categories, y_span, flipped, base_size, font, state)
    # 标注高度并入轴范围，避免最高一组的括线/字母被面板边缘裁掉
    tops <- s$mean + ifelse(is.finite(s$error), s$error, 0)
    n_pair_rows <- if (is.null(sig$pairs)) 0L else nrow(sig$pairs)
    axis_values <- c(axis_values, max(tops, na.rm = TRUE) + (0.035 + 0.055 * n_pair_rows + 0.075) * y_span)
    state$analysis$significance_rendered <- list(
      method = sig$method, source = "case-supplied results rendered verbatim; no statistical test computed by this Skill",
      n_pair_marks = n_pair_rows,
      n_group_marks = if (is.null(sig$letters)) 0L else nrow(sig$letters),
      layout = if (flipped) "end-of-bar marks (horizontal layout)" else "brackets above groups"
    )
  }
  if (!is.null(sig_layers)) p <- p + sig_layers
  p <- p + continuous_y_scale(axis_values, include_zero = bar_mode, n = 4, log_scale = log_applied)
  extra <- NULL
  if (length(categories) > 8L) {
    p <- p + ggplot2::coord_flip()
    add_note(state, "More than eight categories were retained and shown with a horizontal layout; no category was truncated.")
    # 行高不足时按行距收缩类别标签字号（下限 6pt），避免相邻行标签重叠
    height_mm_cfg <- cfg_number(cfg, "height_mm", 62, lower = 20)
    row_mm <- (height_mm_cfg - 22) / length(categories)
    category_pt <- max(6, min(base_size, floor(0.92 * row_mm / 0.423)))
    if (category_pt < base_size) {
      extra <- ggplot2::theme(axis.text.y = ggplot2::element_text(size = category_pt))
      add_note(state, sprintf("Category labels were shrunk to %d pt to fit %d rows without overlap (floor 6 pt).", category_pt, length(categories)))
    }
  } else {
    extra <- categorical_rotate_theme(categories, cfg_number(cfg, "width_mm", 89, lower = 20), base_size)
    if (!is.null(extra)) add_note(state, "Predicted x-category label overlap: labels were rotated 45 degrees rather than truncated or shrunk.")
  }
  caption <- if (comparison) {
    paste0("Automatic comparison view: ", actual, "; min group n=", min_n, ". ",
           if (display_error == "none") "No mean uncertainty displayed." else bar_caption(display_error, "mean"))
  } else if (!is.null(explicit_type)) {
    paste0("Explicit distribution view: ", actual, "; min group n=", min_n,
           "; points are raw observations and no mean error bars are overlaid.")
  } else bar_caption(display_error)
  if (unit_mode) {
    caption <- gsub("Points: raw observations", "Points: independent-unit means", caption, fixed = TRUE)
    caption <- gsub("points are raw observations", "points are independent-unit means", caption, fixed = TRUE)
  }
  p <- finish_plot(p, cfg, font, base_size, state, show_legend = FALSE, caption = caption, extra_theme = extra)
  state$analysis$actual_chart_type <- actual
  state$analysis$groups <- categories
  state$analysis$series <- summaries$records
  if (unit_mode) {
    state$analysis$replicates <- list(
      unit_column = "unit_id", aggregation = "arithmetic mean within x + unit_id",
      technical_readings = raw_reading_count, independent_units = nrow(d), unit_means = unit_means
    )
  }
  state$analysis$palette <- unname(palette_for(categories, cfg))
  state$analysis$point_style <- list(
    shape = 21, fill = "category palette (same as bars/boxes)", stroke = "white",
    alpha = round(point_alpha, 2), size_mm = round(point_size, 2),
    note = "raw observations coloured by category with white stroke; alpha shrinks with point count")
  state$analysis$error_bar <- list(requested = requested_error, displayed = display_error,
                                   ci95_definition = "two-sided t interval half-width; null for n=1")
  state$analysis$distribution <- list(min_n = unname(min_n), max_n = max(table(d$x)),
                                      selection_rule = if (comparison) "min_n<3 points; 3≤min_n<10 points+mean; min_n≥10 boxplot+jitter" else if (is.null(explicit_type)) "explicit mean bar + raw points" else "explicit box/violin geometry for min_n≥3; points only below 3")
  list(plot = p)
}

fit_one <- function(data) {
  n <- nrow(data)
  base <- list(n = n, slope = NULL, intercept = NULL, r = NULL, r_squared = NULL,
               status = "not_fitted", reason = NULL)
  if (n < 3L) {
    base$reason <- "n<3: scatter points are shown without an OLS fit or 95% confidence band."
    return(base)
  }
  if (length(unique(data$x_num)) < 2L || stats::sd(data$x_num) == 0 ||
      length(unique(data$y)) < 2L || stats::sd(data$y) == 0) {
    base$status <- "zero_variance"
    base$reason <- "x or y has zero variance: slope and Pearson r are undefined for this figure contract; only points are shown."
    return(base)
  }
  model <- tryCatch(stats::lm(y ~ x_num, data = data), error = function(e) e)
  if (inherits(model, "error")) {
    base$status <- "error"
    base$reason <- conditionMessage(model)
    return(base)
  }
  coefficient <- stats::coef(model)
  r_value <- suppressWarnings(stats::cor(data$x_num, data$y, method = "pearson"))
  base$slope <- null_na(unname(coefficient[["x_num"]]))
  base$intercept <- null_na(unname(coefficient[[1L]]))
  base$r <- null_na(r_value)
  base$r_squared <- null_na(if (is.finite(r_value)) r_value^2 else NA_real_)
  base$status <- "ok"
  base$reason <- NULL
  base$model <- model
  base
}

build_scatter <- function(raw, cfg, state, font, base_size) {
  xy <- get_xy(raw, cfg, state, need_x_num = TRUE, need_y = TRUE)
  group_requested <- nzchar(cfg_text(cfg, "group", ""))
  group <- as.character(standard_column(raw, "group", "group", cfg, required = FALSE))
  used <- is.finite(xy$x_num) & is.finite(xy$y)
  if (group_requested) used <- used & valid_category(group)
  set_counts(state, raw, used)
  temporal <- temporal_x_info(xy$x, used, xy$x_num, state)
  if (!any(used)) stop("No rows with finite x_num and y", if (group_requested) " plus a non-empty group" else "")
  unit_id <- as.character(standard_column(raw, "unit_id", "unit_id", cfg, required = FALSE))
  if (any(nonempty(unit_id[used]))) add_warning(state, "unit_id is present: OLS treats rows as observations for description only; independence, technical replication and repeated-measures correlation require a separately justified model.")
  d <- data.frame(x_num = xy$x_num[used], y = xy$y[used], group = group[used],
                  .row_id = row_ids(raw)[used], stringsAsFactors = FALSE)
  fit_group <- cfg_bool(cfg, "fit_group", FALSE)
  groups <- if (group_requested) unique(d$group) else character()
  d$.group <- if (group_requested) factor(d$group, levels = groups) else factor(rep("All", nrow(d)), levels = "All")
  point_size <- density_point_size(base_size, cfg, nrow(d))

  if (group_requested) {
    p <- ggplot2::ggplot(d, ggplot2::aes(x = .data[["x_num"]], y = .data[["y"]],
                                         colour = .data[[".group"]], shape = .data[[".group"]])) +
      ggplot2::geom_point(size = point_size, alpha = 0.8, stroke = 0.6) +
      ggplot2::scale_colour_manual(values = palette_for(groups, cfg),
                                   labels = stats::setNames(wrap_text(groups), groups), drop = FALSE) +
      ggplot2::scale_shape_manual(values = rep(c(16, 17, 15, 18, 3, 4, 8, 7), length.out = length(groups)),
                                  labels = stats::setNames(wrap_text(groups), groups), drop = FALSE)
  } else {
    p <- ggplot2::ggplot(d, ggplot2::aes(x = .data[["x_num"]], y = .data[["y"]])) +
      ggplot2::geom_point(size = point_size, alpha = 0.8, colour = unname(palette_for("All", cfg)[[1L]]), shape = 16, stroke = 0.6)
  }

  fit_sets <- if (fit_group && group_requested) split(d, d$group, drop = TRUE) else list(All = d)
  fit_records <- list()
  predictions <- list()
  prediction_index <- 0L
  for (name in names(fit_sets)) {
    fit_data <- fit_sets[[name]]
    fit <- fit_one(fit_data)
    if (fit_group && group_requested) fit$group <- name
    fit_records[[name]] <- fit
    if (fit$status == "ok") {
      newdata <- data.frame(x_num = seq(min(fit_data$x_num), max(fit_data$x_num), length.out = 100L))
      predicted <- tryCatch(stats::predict(fit$model, newdata = newdata, interval = "confidence", level = 0.95),
                            error = function(e) NULL)
      if (!is.null(predicted) && all(is.finite(predicted))) {
        prediction_index <- prediction_index + 1L
        predictions[[prediction_index]] <- data.frame(
          x_num = newdata$x_num, fit = predicted[, "fit"], lwr = predicted[, "lwr"],
          upr = predicted[, "upr"], .group = name, stringsAsFactors = FALSE
        )
      } else {
        fit_records[[name]]$status <- "fit_band_error"
        fit_records[[name]]$reason <- "OLS coefficients were calculated but a finite 95% confidence band could not be predicted; fit line was omitted."
        fit_records[[name]]$slope <- NULL
        fit_records[[name]]$intercept <- NULL
        fit_records[[name]]$r <- NULL
        fit_records[[name]]$r_squared <- NULL
      }
    }
    fit_records[[name]]$model <- NULL
  }
  if (length(predictions)) {
    pred <- do.call(rbind, predictions)
    pred$.group <- factor(pred$.group, levels = names(fit_sets))
    if (fit_group && group_requested) {
      p <- p + ggplot2::geom_ribbon(
        data = pred, ggplot2::aes(x = .data[["x_num"]], ymin = .data[["lwr"]], ymax = .data[["upr"]],
                                  fill = .data[[".group"]]), inherit.aes = FALSE, alpha = 0.18,
        colour = NA, show.legend = FALSE
      ) + ggplot2::scale_fill_manual(values = palette_for(groups, cfg), drop = FALSE) +
        ggplot2::geom_line(data = pred, ggplot2::aes(x = .data[["x_num"]], y = .data[["fit"]],
                                                      colour = .data[[".group"]], group = .data[[".group"]]),
                           inherit.aes = FALSE, linewidth = 0.6, show.legend = FALSE)
    } else {
      p <- p + ggplot2::geom_ribbon(
        data = pred, ggplot2::aes(x = .data[["x_num"]], ymin = .data[["lwr"]], ymax = .data[["upr"]]),
        inherit.aes = FALSE, fill = "#999999", alpha = 0.25, colour = NA
      ) + ggplot2::geom_line(data = pred, ggplot2::aes(x = .data[["x_num"]], y = .data[["fit"]]),
                             inherit.aes = FALSE, linewidth = 0.6, colour = "black")
    }
  }
  p <- p + ggplot2::labs(
    x = axis_label(cfg_text(cfg, "x_label", cfg_text(cfg, "x", "x_num")), cfg_text(cfg, "x_unit", "")),
    y = axis_label(cfg_text(cfg, "y_label", cfg_text(cfg, "y", "y")), cfg_text(cfg, "unit", "")),
    colour = cfg_text(cfg, "group_label", cfg_text(cfg, "group", "Group")), shape = cfg_text(cfg, "group_label", cfg_text(cfg, "group", "Group"))
  ) + continuous_x_scale(d$x_num, include_zero = FALSE, labels = temporal$labels)
  band_values <- if (length(predictions)) {
    pred <- do.call(rbind, predictions)
    c(pred$lwr, pred$upr)
  } else numeric()
  log_axis_state(d$y, cfg_bool(cfg, "log_y", FALSE), cfg, state)
  log_applied_scat <- !is.null(state$analysis$y_scale) && isTRUE(state$analysis$y_scale$applied)
  p <- p + continuous_y_scale(c(d$y, band_values), include_zero = FALSE, log_scale = log_applied_scat)
  if (group_requested && length(groups) > 6L) {
    p <- p + ggplot2::facet_wrap(. ~ .group)
    add_note(state, "More than six groups were retained in small multiples; repeated colours are separated by facet identity.")
  }
  p <- finish_plot(p, cfg, font, base_size, state, show_legend = group_requested && length(groups) <= 6L,
                   caption = if (fit_group && group_requested) "Group-specific OLS with pointwise 95% confidence bands where defined." else "Overall OLS with pointwise 95% confidence band where defined; groups do not trigger separate fits unless fit_group=true.")
  state$analysis$actual_chart_type <- "scatter_with_fit"
  state$analysis$x_scale <- temporal[names(temporal) != "labels"]
  state$analysis$groups <- groups
  state$analysis$fit_group <- fit_group
  state$analysis$fit <- if (fit_group && group_requested) unname(fit_records) else fit_records[["All"]]
  state$analysis$series <- lapply(seq_len(nrow(d)), function(i) list(
    source_row_id = d$.row_id[[i]], x = d$x_num[[i]], y = d$y[[i]],
    group = if (group_requested) d$group[[i]] else NULL
  ))
  list(plot = p)
}

build_line <- function(raw, cfg, state, font, base_size) {
  xy <- get_xy(raw, cfg, state, need_x_num = TRUE, need_y = TRUE)
  group_requested <- nzchar(cfg_text(cfg, "group", ""))
  group <- as.character(standard_column(raw, "group", "group", cfg, required = FALSE))
  used <- is.finite(xy$x_num) & is.finite(xy$y)
  if (group_requested) used <- used & valid_category(group)
  set_counts(state, raw, used)
  temporal <- temporal_x_info(xy$x, used, xy$x_num, state)
  if (!any(used)) stop("No rows with finite x_num and y for line_series")
  unit_id <- as.character(standard_column(raw, "unit_id", "unit_id", cfg, required = FALSE))
  if (any(nonempty(unit_id[used]))) add_warning(state, "unit_id is present: repeated-x mean lines and descriptive error bands do not model unit-level dependence; independence and repeated-measures correlation require a separately justified model.")
  d <- data.frame(x_num = xy$x_num[used], y = xy$y[used], group = group[used],
                  .row_id = row_ids(raw)[used], stringsAsFactors = FALSE)
  groups <- if (group_requested) unique(d$group) else character()
  d$.group <- if (group_requested) d$group else rep("All", nrow(d))
  plot_groups <- if (group_requested) groups else "All"
  d$.group <- factor(d$.group, levels = plot_groups)
  error_type <- parse_error_type(cfg, state, default = "none", warn_missing = FALSE)
  records <- list()
  means <- list()
  k <- 0L
  for (g in plot_groups) {
    gd <- d[d$.group == g, , drop = FALSE]
    xs <- sort(unique(gd$x_num))
    for (x_value in xs) {
      selected <- which(gd$x_num == x_value)
      k <- k + 1L
      record <- summary_one(if (group_requested) paste0(g, " @ x=", format(x_value, digits = 15)) else paste0("x=", format(x_value, digits = 15)),
                            gd$y[selected], gd$.row_id[selected], error_type,
                            extra = list(group = if (group_requested) g else NULL, x = x_value))
      records[[k]] <- record
      means[[k]] <- data.frame(
        .group = g, x_num = x_value, n = record$n, mean = record$mean %||% NA_real_,
        error = record$error %||% NA_real_, stringsAsFactors = FALSE
      )
    }
  }
  warn_singleton_uncertainty(records, error_type, state)
  m <- do.call(rbind, means)
  m <- m[order(m$.group, m$x_num), , drop = FALSE]
  m$.group <- factor(m$.group, levels = plot_groups)
  point_size <- density_point_size(base_size, cfg, nrow(d), floor_mm = 1.0)
  if (group_requested) {
    p <- ggplot2::ggplot(d, ggplot2::aes(x = .data[["x_num"]], y = .data[["y"]],
                                         colour = .data[[".group"]], shape = .data[[".group"]])) +
      ggplot2::scale_colour_manual(values = palette_for(groups, cfg),
                                   labels = stats::setNames(wrap_text(groups), groups), drop = FALSE) +
      ggplot2::scale_shape_manual(values = rep(c(16, 17, 15, 18, 3, 4, 8, 7), length.out = length(groups)),
                                  labels = stats::setNames(wrap_text(groups), groups), drop = FALSE) +
      ggplot2::scale_linetype_manual(values = rep(c("solid", "dashed", "dotdash", "twodash", "longdash", "dotted"), length.out = length(groups)),
                                     labels = stats::setNames(wrap_text(groups), groups), drop = FALSE)
  } else {
    p <- ggplot2::ggplot(d, ggplot2::aes(x = .data[["x_num"]], y = .data[["y"]]))
  }
  band <- m[is.finite(m$error) & m$n >= 2L, , drop = FALSE]
  if (error_type != "none" && nrow(band)) {
    if (group_requested) {
      p <- p + ggplot2::geom_ribbon(
        data = band, ggplot2::aes(x = .data[["x_num"]], ymin = .data[["mean"]] - .data[["error"]],
                                  ymax = .data[["mean"]] + .data[["error"]], fill = .data[[".group"]],
                                  group = .data[[".group"]]), inherit.aes = FALSE,
        alpha = 0.18, colour = NA, show.legend = FALSE
      ) + ggplot2::scale_fill_manual(values = palette_for(groups, cfg), drop = FALSE)
    } else {
      p <- p + ggplot2::geom_ribbon(
        data = band, ggplot2::aes(x = .data[["x_num"]], ymin = .data[["mean"]] - .data[["error"]],
                                  ymax = .data[["mean"]] + .data[["error"]], group = .data[[".group"]]),
        inherit.aes = FALSE, fill = "#999999", alpha = 0.25, colour = NA
      )
    }
  }
  if (group_requested) {
    p <- p + ggplot2::geom_line(
      data = m, ggplot2::aes(x = .data[["x_num"]], y = .data[["mean"]], colour = .data[[".group"]],
                             linetype = .data[[".group"]], group = .data[[".group"]]),
      inherit.aes = FALSE, linewidth = 0.6
    ) + ggplot2::geom_point(size = point_size, alpha = 0.8, stroke = 0.6)
  } else {
    p <- p + ggplot2::geom_line(data = m, ggplot2::aes(x = .data[["x_num"]], y = .data[["mean"]]),
                                inherit.aes = FALSE, linewidth = 0.6, colour = unname(palette_for("All", cfg)[[1L]])) +
      ggplot2::geom_point(size = point_size, alpha = 0.8, colour = unname(palette_for("All", cfg)[[1L]]), shape = 16)
  }
  width_mm_cfg <- cfg_number(cfg, "width_mm", 89, lower = 20)
  x_break_targets <- max(3L, min(8L, floor(width_mm_cfg / 18)))
  p <- p + ggplot2::labs(
    x = axis_label(cfg_text(cfg, "x_label", cfg_text(cfg, "x", "x_num")), cfg_text(cfg, "x_unit", "")),
    y = axis_label(cfg_text(cfg, "y_label", cfg_text(cfg, "y", "y")), cfg_text(cfg, "unit", "")),
    colour = cfg_text(cfg, "group_label", cfg_text(cfg, "group", "Group")), shape = cfg_text(cfg, "group_label", cfg_text(cfg, "group", "Group")),
    linetype = cfg_text(cfg, "group_label", cfg_text(cfg, "group", "Group"))
  ) + continuous_x_scale(d$x_num, include_zero = FALSE, labels = temporal$labels, n = x_break_targets)
  log_axis_state(d$y, cfg_bool(cfg, "log_y", FALSE), cfg, state)
  log_applied_line <- !is.null(state$analysis$y_scale) && isTRUE(state$analysis$y_scale$applied)
  p <- p + continuous_y_scale(c(d$y, m$mean - m$error, m$mean + m$error), include_zero = FALSE, log_scale = log_applied_line)
  definition <- if (error_type == "none") "no uncertainty band requested" else paste0("band: ±", switch(error_type, sd = "1 SD", se = "1 SE", ci95 = "two-sided 95% t CI half-width"), " at repeated-x cells with n≥2")
  if (group_requested && length(groups) > 6L) {
    p <- p + ggplot2::facet_wrap(. ~ .group)
    add_note(state, "More than six groups were retained in small multiples; repeated colours/line types are separated by facet identity.")
  }
  p <- finish_plot(p, cfg, font, base_size, state, show_legend = group_requested && length(groups) <= 6L,
                   caption = paste0("Lines: arithmetic means at each numeric x; points: raw observations; ", definition, "."))
  state$analysis$actual_chart_type <- "line_series"
  state$analysis$x_scale <- temporal[names(temporal) != "labels"]
  state$analysis$x_scale$break_policy <- sprintf(
    "pretty breaks capped at %d targets for a %.0f mm canvas; two-line compact labels prevent date-tick overlap",
    x_break_targets, width_mm_cfg
  )
  state$analysis$groups <- groups
  state$analysis$series <- records
  state$analysis$error_bar <- list(requested = error_type, displayed = if (nrow(band)) error_type else "none")
  state$analysis$line <- list(repeated_x_aggregated = any(m$n > 1L), n_cells = nrow(m),
                              ordering = "increasing x_num within group")
  list(plot = p)
}

build_histogram <- function(raw, cfg, state, font, base_size) {
  xy <- get_xy(raw, cfg, state, need_x_num = TRUE, need_y = FALSE)
  group_requested <- nzchar(cfg_text(cfg, "group", ""))
  group <- as.character(standard_column(raw, "group", "group", cfg, required = FALSE))
  used <- is.finite(xy$x_num)
  if (group_requested) used <- used & valid_category(group)
  set_counts(state, raw, used)
  if (!any(used)) stop("No finite x_num values for histogram")
  d <- data.frame(x_num = xy$x_num[used], group = group[used], .row_id = row_ids(raw)[used],
                  stringsAsFactors = FALSE)
  groups <- if (group_requested) unique(d$group) else character()
  plot_groups <- if (group_requested) groups else "All"
  rectangles <- list()
  distributions <- list()
  for (i in seq_along(plot_groups)) {
    g <- plot_groups[[i]]
    values <- if (group_requested) d$x_num[d$group == g] else d$x_num
    n <- length(values)
    bins <- max(1L, ceiling(log2(n) + 1))
    if (min(values) == max(values)) {
      delta <- max(0.5, abs(values[[1L]]) * 0.05)
      breaks <- seq(values[[1L]] - delta, values[[1L]] + delta, length.out = bins + 1L)
    } else {
      breaks <- seq(min(values), max(values), length.out = bins + 1L)
    }
    h <- graphics::hist(values, breaks = breaks, plot = FALSE, include.lowest = TRUE, right = TRUE)
    label <- paste0(g, " (n=", n, ")")
    rectangles[[i]] <- data.frame(
      xmin = h$breaks[-length(h$breaks)], xmax = h$breaks[-1L], count = h$counts,
      facet = label, stringsAsFactors = FALSE
    )
    distributions[[i]] <- list(
      category = if (group_requested) g else "All", n = n, bins = bins,
      bin_method = "Sturges: ceiling(log2(n)+1)", breaks = h$breaks,
      counts = h$counts, mean = null_na(mean(values)), sd = null_na(if (n >= 2L) stats::sd(values) else NA_real_),
      min = min(values), max = max(values),
      source_row_ids = I(if (group_requested) d$.row_id[d$group == g] else d$.row_id)
    )
  }
  r <- do.call(rbind, rectangles)
  facet_levels <- vapply(distributions, function(x) paste0(x$category, " (n=", x$n, ")"), character(1))
  r$facet <- factor(r$facet, levels = facet_levels)
  p <- ggplot2::ggplot(r, ggplot2::aes(xmin = .data[["xmin"]], xmax = .data[["xmax"]],
                                       ymin = 0, ymax = .data[["count"]])) +
    ggplot2::geom_rect(fill = unname(palette_for("All", cfg)[[1L]]), colour = "white", linewidth = 0.6) +
    continuous_y_scale(r$count, include_zero = TRUE) +
    ggplot2::labs(
      x = axis_label(cfg_text(cfg, "x_label", cfg_text(cfg, "x", "x_num")), cfg_text(cfg, "x_unit", "")),
      y = "Count"
    )
  if (group_requested) {
    p <- p + ggplot2::facet_wrap(. ~ facet, ncol = min(length(groups), 3L), scales = "fixed")
    caption <- "Facets show group and n; each facet uses its own Sturges bin count on a shared scale."
  } else {
    caption <- paste0("n=", nrow(d), "; bins=", distributions[[1L]]$bins, " (Sturges).")
  }
  p <- finish_plot(p, cfg, font, base_size, state, show_legend = FALSE, caption = caption)
  state$analysis$actual_chart_type <- "histogram"
  state$analysis$groups <- groups
  state$analysis$distribution <- if (group_requested) distributions else distributions[[1L]]
  state$analysis$series <- distributions
  list(plot = p)
}

build_stacked <- function(raw, cfg, state, font, base_size) {
  xy <- get_xy(raw, cfg, state, need_x_num = FALSE, need_y = TRUE)
  group <- as.character(standard_column(raw, "group", "group", cfg, required = FALSE))
  all_group_empty <- !any(valid_category(group))
  used_base <- valid_category(xy$x) & is.finite(xy$y)
  used <- if (all_group_empty) used_base else used_base & valid_category(group)
  set_counts(state, raw, used)
  if (!all_group_empty && any(used_base & !valid_category(group))) {
    add_warning(state, "Rows with missing group were excluded from the stacked composition; they were not assigned to a fabricated group.")
  }
  if (!any(used)) stop("No rows with non-empty x and finite y for stacked_bar")
  d <- data.frame(x = xy$x[used], y = xy$y[used], group = group[used], .row_id = row_ids(raw)[used],
                  stringsAsFactors = FALSE)

  if (all_group_empty) {
    categories <- unique(d$x)
    composition <- lapply(categories, function(category) {
      selected <- which(d$x == category)
      list(category = category, n = length(selected), value = sum(d$y[selected]),
           source_row_ids = I(d$.row_id[selected]))
    })
    totals <- data.frame(category = categories,
                         value = vapply(composition, `[[`, numeric(1), "value"),
                         stringsAsFactors = FALSE)
    totals <- totals[order(totals$value, totals$category), , drop = FALSE]
    totals$category <- factor(totals$category, levels = totals$category)
    p <- ggplot2::ggplot(totals, ggplot2::aes(x = .data[["value"]], y = .data[["category"]])) +
      ggplot2::geom_col(fill = unname(palette_for("All", cfg)[[1L]]), colour = "black", linewidth = 0.6, width = 0.65) +
      continuous_x_scale(totals$value, include_zero = TRUE) +
      ggplot2::scale_y_discrete(labels = wrap_text) +
      ggplot2::labs(
        x = axis_label(cfg_text(cfg, "y_label", cfg_text(cfg, "y", "Total")), cfg_text(cfg, "unit", "")),
        y = axis_label(cfg_text(cfg, "x_label", cfg_text(cfg, "x", "x")), cfg_text(cfg, "x_unit", ""))
      )
    p <- finish_plot(p, cfg, font, base_size, state, show_legend = FALSE,
                     caption = "All group values were empty: rows were aggregated to totals by x and sorted in horizontal bars.")
    state$analysis$actual_chart_type <- "horizontal_total_bar"
    state$analysis$groups <- character()
    state$analysis$composition <- list(mode = "x_totals_because_group_empty", totals = composition)
    state$analysis$series <- composition
    add_warning(state, "group was empty for every row: stacked_bar was rendered as sorted horizontal total bars by x.")
    return(list(plot = p))
  }

  categories <- unique(d$x)
  groups <- unique(d$group)
  d$x <- factor(d$x, levels = categories)
  d$group <- factor(d$group, levels = groups)
  composition <- list()
  k <- 0L
  for (category in categories) {
    for (g in groups) {
      selected <- which(d$x == category & d$group == g)
      if (!length(selected)) next
      k <- k + 1L
      composition[[k]] <- list(category = category, group = g, n = length(selected),
                               value = sum(d$y[selected]), source_row_ids = I(d$.row_id[selected]))
    }
  }
  totals <- vapply(categories, function(category) sum(d$y[d$x == category]), numeric(1))
  positive_totals <- vapply(categories, function(category) sum(pmax(0, d$y[d$x == category])), numeric(1))
  negative_totals <- vapply(categories, function(category) sum(pmin(0, d$y[d$x == category])), numeric(1))
  p <- ggplot2::ggplot(d, ggplot2::aes(x = .data[["x"]], y = .data[["y"]], fill = .data[["group"]])) +
    ggplot2::geom_col(position = ggplot2::position_stack(), width = 0.65,
                      colour = "black", linewidth = 0.6, alpha = 0.8) +
    ggplot2::scale_fill_manual(values = palette_for(groups, cfg),
                               labels = stats::setNames(wrap_text(groups), groups), drop = FALSE) +
    ggplot2::guides(fill = ggplot2::guide_legend(ncol = 1, byrow = TRUE,
                                                 title = cfg_text(cfg, "group_label", cfg_text(cfg, "group", "Group")))) +
    ggplot2::scale_x_discrete(labels = stats::setNames(wrap_text(categories), categories)) +
    continuous_y_scale(c(totals, positive_totals, negative_totals), include_zero = TRUE) +
    ggplot2::labs(
      x = axis_label(cfg_text(cfg, "x_label", cfg_text(cfg, "x", "x")), cfg_text(cfg, "x_unit", "")),
      y = axis_label(cfg_text(cfg, "y_label", cfg_text(cfg, "y", "y")), cfg_text(cfg, "unit", "")),
      fill = cfg_text(cfg, "group_label", cfg_text(cfg, "group", "Group"))
    )
  if (length(categories) > 8L) {
    p <- p + ggplot2::coord_flip()
    add_note(state, "More than eight composition categories were retained with a horizontal stacked layout; no category was truncated.")
  }
  p <- finish_plot(p, cfg, font, base_size, state, show_legend = TRUE,
                   caption = "Stacked values are row-level y measurements; axis ticks cover positive and negative stack extents and x totals.",
                   extra_theme = if (length(categories) <= 8L)
                     categorical_rotate_theme(categories, cfg_number(cfg, "width_mm", 89, lower = 20), base_size) else NULL)
  state$analysis$actual_chart_type <- "stacked_bar"
  state$analysis$groups <- groups
  state$analysis$composition <- list(mode = "stacked", segments = composition,
                                     totals = lapply(seq_along(categories), function(i) list(category = categories[[i]], total = totals[[i]])))
  state$analysis$series <- composition
  list(plot = p)
}

build_category_count <- function(raw, cfg, state, font, base_size) {
  x <- as.character(standard_column(raw, "x", "x", cfg, required = TRUE))
  used <- valid_category(x)
  set_counts(state, raw, used)
  if (!any(used)) stop("No non-empty x categories for category_count")
  d <- data.frame(x = x[used], .row_id = row_ids(raw)[used], stringsAsFactors = FALSE)
  categories <- unique(d$x)
  records <- lapply(categories, function(category) {
    selected <- which(d$x == category)
    list(category = category, n = length(selected), count = length(selected),
         source_row_ids = I(d$.row_id[selected]))
  })
  counts <- data.frame(category = categories,
                       count = vapply(records, `[[`, integer(1), "n"), stringsAsFactors = FALSE)
  counts <- counts[order(counts$count, counts$category), , drop = FALSE]
  counts$category <- factor(counts$category, levels = counts$category)
  p <- ggplot2::ggplot(counts, ggplot2::aes(x = .data[["count"]], y = .data[["category"]])) +
    ggplot2::geom_col(fill = unname(palette_for("All", cfg)[[1L]]), colour = "black", linewidth = 0.6, width = 0.65) +
    continuous_x_scale(counts$count, include_zero = TRUE) +
    ggplot2::scale_y_discrete(labels = wrap_text) +
    ggplot2::labs(
      x = "Count",
      y = axis_label(cfg_text(cfg, "x_label", cfg_text(cfg, "x", "x")), cfg_text(cfg, "x_unit", ""))
    )
  p <- finish_plot(p, cfg, font, base_size, state, show_legend = FALSE,
                   caption = paste0("Counts of non-empty x categories; total n=", nrow(d), "."))
  state$analysis$actual_chart_type <- "category_count"
  state$analysis$groups <- categories
  state$analysis$series <- records
  state$analysis$distribution <- list(variable = cfg_text(cfg, "x", "x"), total = nrow(d), counts = records)
  list(plot = p)
}

build_heatmap <- function(raw, cfg, state, font, base_size) {
  variables <- unlist(cfg_value(cfg, "variables", character()), use.names = FALSE)
  variables <- as.character(variables[nonempty(variables)])
  variables <- unique(variables)
  if (length(variables) < 2L) stop("correlation_heatmap requires at least two named variables")
  missing_columns <- variables[!variables %in% names(raw)]
  if (length(missing_columns)) stop("Heatmap TSV is missing variable column(s): ", paste(missing_columns, collapse = ", "))
  # 数据字典短标签优先（如 "Cement (component 1)(kg...)" → "Cement"），找不到回退原始列名
  variable_labels <- cfg_value(cfg, "variable_labels", list())
  if (!is.list(variable_labels)) variable_labels <- list()
  display_name <- function(variable) {
    hit <- variable_labels[[variable]]
    text <- if (is.null(hit)) "" else trimws(as.character(hit)[[1L]])
    if (!nzchar(text) || is.na(text)) variable else text
  }
  display_names <- vapply(variables, display_name, character(1), USE.NAMES = FALSE)
  values <- lapply(variables, function(variable) numeric_vector(raw[[variable]], variable, state))
  names(values) <- variables
  finite_matrix <- do.call(cbind, lapply(values, is.finite))
  used <- rowSums(finite_matrix) >= 2L
  set_counts(state, raw, used)
  pairs <- list()
  tiles <- list()
  k <- 0L
  for (i in seq_along(variables)) {
    for (j in seq_along(variables)) {
      x <- values[[i]]
      y <- values[[j]]
      complete <- is.finite(x) & is.finite(y)
      n <- sum(complete)
      r <- NA_real_
      status <- "ok"
      reason <- NULL
      if (n < 2L) {
        status <- "insufficient_complete_pairs"
        reason <- "Fewer than two pairwise-complete finite observations."
      } else if (length(unique(x[complete])) < 2L || length(unique(y[complete])) < 2L ||
                 stats::sd(x[complete]) == 0 || stats::sd(y[complete]) == 0) {
        status <- "zero_variance"
        reason <- "At least one variable is constant on this pairwise-complete subset; Pearson r is null."
      } else {
        r <- suppressWarnings(stats::cor(x[complete], y[complete], method = "pearson"))
        if (!is.finite(r)) {
          status <- "undefined"
          reason <- "Pearson correlation did not produce a finite value."
          r <- NA_real_
        }
      }
      k <- k + 1L
      pairs[[k]] <- list(variable_x = variables[[i]], variable_y = variables[[j]],
                         n = n, r = null_na(r), status = status, reason = reason)
      tiles[[k]] <- data.frame(variable_x = variables[[i]], variable_y = variables[[j]],
                               correlation = r, n = n, label = if (is.finite(r)) sprintf("%.2f", r) else "NA",
                               stringsAsFactors = FALSE)
    }
  }
  tile <- do.call(rbind, tiles)
  tile$variable_x <- factor(tile$variable_x, levels = variables)
  tile$variable_y <- factor(tile$variable_y, levels = rev(variables))
  matrix_rows <- lapply(seq_along(variables), function(i) {
    row <- lapply(seq_along(variables), function(j) {
      index <- (i - 1L) * length(variables) + j
      pairs[[index]]$r
    })
    names(row) <- NULL
    row
  })
  matrix_rows <- unname(matrix_rows)
  constant_variables <- variables[vapply(variables, function(variable) {
    x <- values[[variable]][is.finite(values[[variable]])]
    length(x) > 0L && length(unique(x)) <= 1L
  }, logical(1))]
  # 标签排版自适应：
  # 0) 几何量先算（格宽同时驱动格内数字与轴标签字号）；
  # 1) 过长标签剥掉括号内单位/说明（单位语义保留在图注与 analysis JSON）；
  # 2) 45° 用窄折行；90°（垂直）用宽折行，把多行宽度压到 1-2 行；
  # 3) 标签字号按格宽 × 最大行数收缩（下限 6pt），保证相邻标签不重叠。
  width_mm_cfg <- cfg_number(cfg, "width_mm", 89, lower = 20)
  height_mm_cfg <- cfg_number(cfg, "height_mm", 62, lower = 20)
  char_mm <- 0.62 * base_size * 0.3528
  panel_est <- min(width_mm_cfg - 55, height_mm_cfg - 55)
  cell_mm <- panel_est / max(1L, length(variables))
  # geom_text 的 size 单位是 mm（≈0.353pt）：按格宽给字，上限 ~9pt、下限 ~4.5pt，
  # 不再随 base_size 线性放大，避免大画布格内数字撑爆格子。
  cell_text_mm <- max(1.6, min(cell_mm * 0.38, 3.2))
  show_cell_text <- length(variables) <= 12L && cell_mm >= 5.5
  p <- ggplot2::ggplot(tile, ggplot2::aes(x = .data[["variable_x"]], y = .data[["variable_y"]],
                                          fill = .data[["correlation"]])) +
    ggplot2::geom_tile(colour = "white", linewidth = 0.6)
  if (show_cell_text) {
    p <- p + ggplot2::geom_text(ggplot2::aes(label = .data[["label"]]), family = font$family,
                                fontface = "bold", size = cell_text_mm, colour = "black")
  } else {
    add_note(state, sprintf(
      "%d variables with ~%.1f mm cells: per-cell value labels were omitted because cells are too small to stay legible; the diverging colour scale and colorbar remain the quantitative encoding.",
      length(variables), cell_mm
    ))
  }
  label_angle <- if (length(variables) * 14 * char_mm * 0.707 > panel_est) 90 else 45
  heat_names <- display_names
  long_labels <- nchar(display_names, type = "width") > 16L
  heat_names[long_labels] <- gsub("\\s*\\([^)]*\\)", "", heat_names[long_labels])
  too_short <- nchar(heat_names, type = "width") < 4L & long_labels
  heat_names[too_short] <- display_names[too_short]
  x_wrap <- if (label_angle == 90) 20L else 14L
  x_labels <- wrap_text(heat_names, width = x_wrap)
  y_labels <- wrap_text(heat_names, width = 14L)
  count_lines <- function(texts) {
    parts <- strsplit(texts, "\n", fixed = TRUE)
    max(vapply(parts, function(ls) max(length(ls), 1L), integer(1)))
  }
  max_lines <- max(count_lines(x_labels), count_lines(y_labels))
  label_pt <- max(6, min(base_size, floor(0.95 * cell_mm / (max(1L, max_lines) * 0.423))))
  p <- p + ggplot2::scale_fill_gradient2(
      low = "#4575B4", mid = "white", high = "#D73027", midpoint = 0,
      limits = c(-1, 1), breaks = seq(-1, 1, by = 0.5), na.value = "#D9D9D9",
      name = "Pearson r", oob = function(x, ...) x
    ) +
    ggplot2::guides(fill = ggplot2::guide_colorbar(
      title.position = "top", barwidth = grid::unit(12, "points"),
      barheight = grid::unit(70, "points")
    )) +
    ggplot2::scale_x_discrete(labels = stats::setNames(x_labels, variables)) +
    ggplot2::scale_y_discrete(labels = stats::setNames(y_labels, rev(variables))) +
    ggplot2::coord_fixed() +
    ggplot2::labs(x = NULL, y = NULL)
  p <- finish_plot(p, cfg, font, base_size, state, show_legend = TRUE,
                   caption = "Pearson correlations on pairwise-complete finite rows; grey NA cells indicate insufficient pairs or zero variance.",
                   extra_theme = ggplot2::theme(
                     axis.text = ggplot2::element_text(size = label_pt),
                     axis.text.x = ggplot2::element_text(angle = label_angle, hjust = 1, vjust = 0.5)
                   ))
  state$analysis$actual_chart_type <- "correlation_heatmap"
  state$analysis$groups <- character()
  state$analysis$heatmap <- list(
    variables = I(variables), display_labels = I(stats::setNames(display_names, variables)),
    axis_labels = I(stats::setNames(heat_names, variables)),
    method = "pearson", use = "pairwise.complete.obs (manual finite-row implementation)",
    limits = c(-1, 1), midpoint = 0, colorbar = TRUE,
    cell_labels = show_cell_text, cell_label_mm = cell_text_mm, cell_mm = cell_mm,
    x_label_angle = label_angle,
    axis_label_size_pt = label_pt,
    label_policy = "括号单位从轴标签剥离（保留在 analysis）；角度按水平投影自适应 45/90；字号按格宽与行数收缩，下限 6pt",
    palette = list(name = "RdBu (blue-white-red diverging)",
                   colors = c("#4575B4", "white", "#D73027"), na_color = "#D9D9D9"),
    matrix = matrix_rows, pairs = pairs,
    constant_variables = I(constant_variables)
  )
  list(plot = p)
}

build_figure <- function(raw, cfg, state, font, base_size) {
  chart_type <- cfg_text(cfg, "chart_type", "")
  switch(
    chart_type,
    bar_with_error_points = build_bar(raw, cfg, state, font, base_size, comparison = FALSE),
    comparison_auto = build_bar(raw, cfg, state, font, base_size, comparison = TRUE),
    scatter_with_fit = build_scatter(raw, cfg, state, font, base_size),
    line_series = build_line(raw, cfg, state, font, base_size),
    histogram = build_histogram(raw, cfg, state, font, base_size),
    stacked_bar = build_stacked(raw, cfg, state, font, base_size),
    category_count = build_category_count(raw, cfg, state, font, base_size),
    correlation_heatmap = build_heatmap(raw, cfg, state, font, base_size),
    boxplot_points = build_bar(raw, cfg, state, font, base_size, comparison = FALSE, explicit_type = "boxplot_points"),
    violin_points = build_bar(raw, cfg, state, font, base_size, comparison = FALSE, explicit_type = "violin_points"),
    stop("Unsupported chart_type '", chart_type, "'")
  )
}

export_three_formats <- function(plot, base_name, width_in, height_in, dpi, family) {
  missing <- c("ggplot2", "svglite")[!vapply(c("ggplot2", "svglite"), requireNamespace, logical(1), quietly = TRUE)]
  if (length(missing)) stop("Missing R package(s): ", paste(missing, collapse = ", "),
                            ". All PDF/PNG/SVG outputs were withheld to avoid a partial-format deliverable.")
  final_paths <- paste0(base_name, c(".pdf", ".png", ".svg"))
  unlink(final_paths)
  temp_base <- tempfile(pattern = paste0(".", basename(base_name), "_"), tmpdir = dirname(base_name))
  temp_paths <- paste0(temp_base, c(".pdf", ".png", ".svg"))
  on.exit(unlink(temp_paths), add = TRUE)
  save_sciplot(plot, temp_base, width = width_in, height = height_in,
               dpi = dpi, family = family, formats = c("pdf", "png", "svg"))
  if (!all(file.exists(temp_paths)) || any(!file.info(temp_paths)$size > 0)) {
    unlink(temp_paths)
    stop("One or more PDF/PNG/SVG devices did not produce a non-empty file")
  }
  for (i in seq_along(final_paths)) {
    if (!file.rename(temp_paths[[i]], final_paths[[i]])) {
      unlink(c(temp_paths, final_paths))
      stop("Could not promote all three temporary exports to final filenames")
    }
  }
  final_paths
}

process_figure <- function(cfg, figure_number, run_dir, state, theme_error) {
  if (!is.null(theme_error)) stop("Could not source RUN_DIR/theme.R: ", theme_error)
  if (!requireNamespace("jsonlite", quietly = TRUE)) stop("R package 'jsonlite' is required")
  data_file <- cfg_text(cfg, "data_file", sprintf("figure_%d.tsv", figure_number))
  data_path <- resolve_path(data_file, run_dir)
  if (!file.exists(data_path)) stop("Data TSV not found: ", data_file)
  raw <- read_tsv(data_path)
  state$analysis$n_input <- nrow(raw)
  state$analysis$n_used <- 0L
  state$analysis$n_excluded <- nrow(raw)
  width_mm <- cfg_number(cfg, "width_mm", 89, lower = 20)
  height_mm <- cfg_number(cfg, "height_mm", 62, lower = 20)
  dpi <- as.integer(round(cfg_number(cfg, "dpi", 600, lower = 72)))
  # 字号取宽与 1.55×高 的较小者驱动：宽扁画布（如 190×62mm 双栏条图）
  # 不得按宽度放大字号，否则文字吃掉画布高度、图形被压成细带。
  base_size <- round(min(width_mm, height_mm * 1.55) / 25.4 * 72 / 27)
  font <- select_font(cfg, raw, state)
  state$analysis$font <- font
  state$analysis$font_family <- font$family
  state$analysis$width_mm <- width_mm
  state$analysis$height_mm <- height_mm
  state$analysis$dpi <- dpi
  state$analysis$base_size <- base_size
  ggplot2::theme_set(theme_sciplot(base_size = base_size, family = font$family))
  set.seed(20261119L + figure_number)
  built <- build_figure(raw, cfg, state, font, base_size)
  significance <- cfg_value(cfg, "significance", NULL)
  if (!is.null(significance) && length(significance)) {
    state$analysis$significance <- significance
    if (is.null(state$analysis$significance_rendered)) {
      add_warning(state, paste0(
        "Significance results were supplied, but this chart type renders no significance marks; ",
        "they were retained verbatim in analysis JSON and the figure caption. ",
        "No statistical test, p-value, significance letters or stars were computed by plot.R."
      ))
    }
  }
  base_name <- file.path(run_dir, sprintf("figure_%d", figure_number))
  files <- export_three_formats(built$plot, base_name, width_mm / 25.4, height_mm / 25.4,
                                dpi, font$family)
  state$analysis$files <- list(
    pdf = basename(files[[1L]]), png = basename(files[[2L]]), svg = basename(files[[3L]])
  )
  state$analysis$status <- "ok"
  invisible(NULL)
}

main <- function() {
  args <- commandArgs(trailingOnly = TRUE)
  run_dir <- if (length(args) >= 1L && nzchar(args[[1L]])) args[[1L]] else getwd()
  run_dir <- normalizePath(run_dir, mustWork = TRUE)
  setwd(run_dir)
  if (!requireNamespace("jsonlite", quietly = TRUE)) {
    stop("R package 'jsonlite' is required to parse run_plan.json and write per-figure analysis JSON")
  }
  plan_path <- file.path(run_dir, "run_plan.json")
  if (!file.exists(plan_path)) stop("run_plan.json not found in ", run_dir)
  plan <- jsonlite::fromJSON(plan_path, simplifyVector = FALSE)
  figures <- plan[["figures"]]
  if (is.null(figures) || !is.list(figures)) stop("run_plan.json must contain a figures array")
  theme_error <- tryCatch({
    source("theme.R")
    NULL
  }, error = function(e) conditionMessage(e))

  for (position in seq_along(figures)) {
    cfg <- figures[[position]]
    if (!is.list(cfg)) cfg <- list()
    figure_number <- suppressWarnings(as.integer(cfg_value(cfg, "index", position)))
    if (length(figure_number) != 1L || is.na(figure_number) || figure_number < 1L) figure_number <- position
    figure_id <- cfg_text(cfg, "figure_id", sprintf("figure_%d", figure_number))
    chart_type <- cfg_text(cfg, "chart_type", "")
    state <- new.env(parent = emptyenv())
    state$warnings <- character()
    state$notes <- character()
    state$analysis <- list(
      figure_id = figure_id, status = "error", chart_type = chart_type,
      actual_chart_type = chart_type, n_input = 0L, n_used = 0L, n_excluded = 0L,
      groups = character(), notes = character(), warnings = character()
    )
    base_name <- file.path(run_dir, sprintf("figure_%d", figure_number))
    unlink(paste0(base_name, c(".pdf", ".png", ".svg")))
    tryCatch(
      withCallingHandlers(
        process_figure(cfg, figure_number, run_dir, state, theme_error),
        warning = function(w) {
          add_warning(state, conditionMessage(w))
          invokeRestart("muffleWarning")
        }
      ),
      error = function(e) {
        unlink(paste0(base_name, c(".pdf", ".png", ".svg")))
        state$analysis$status <- "error"
        state$analysis$reason <- conditionMessage(e)
        if (state$analysis$n_input > 0L && state$analysis$n_used == 0L) {
          state$analysis$n_excluded <- state$analysis$n_input
        }
      }
    )
    state$analysis$notes <- unique(state$notes)
    state$analysis$warnings <- unique(state$warnings)
    analysis_file <- cfg_text(cfg, "analysis_file", sprintf("figure_%d_analysis.json", figure_number))
    analysis_path <- resolve_path(analysis_file, run_dir)
    tryCatch(
      write_analysis_json(state$analysis, analysis_path),
      error = function(e) message("[plot.R] ", conditionMessage(e))
    )
    message("[plot.R] ", figure_id, ": ", state$analysis$status,
            if (!is.null(state$analysis$reason)) paste0(" — ", state$analysis$reason) else "")
  }
  invisible(NULL)
}

if (sys.nframe() == 0L) main()
