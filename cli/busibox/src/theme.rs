use ratatui::style::{Color, Modifier, Style};

pub const BRAND_BLUE: Color = Color::Rgb(66, 135, 245);
pub const BRAND_GREEN: Color = Color::Rgb(72, 199, 142);
pub const BRAND_YELLOW: Color = Color::Rgb(250, 200, 80);
pub const BRAND_RED: Color = Color::Rgb(240, 80, 80);
pub const BRAND_CYAN: Color = Color::Rgb(80, 200, 220);
pub const BRAND_GRAY: Color = Color::Rgb(140, 140, 150);
pub const BRAND_DIM: Color = Color::Rgb(100, 100, 110);
pub const BRAND_BG: Color = Color::Rgb(20, 20, 30);

pub fn title() -> Style {
    Style::default()
        .fg(BRAND_BLUE)
        .add_modifier(Modifier::BOLD)
}

pub fn heading() -> Style {
    Style::default()
        .fg(Color::White)
        .add_modifier(Modifier::BOLD)
}

pub fn normal() -> Style {
    Style::default().fg(Color::White)
}

pub fn dim() -> Style {
    Style::default().fg(BRAND_DIM)
}

pub fn muted() -> Style {
    Style::default().fg(BRAND_GRAY)
}

pub fn success() -> Style {
    Style::default().fg(BRAND_GREEN)
}

pub fn warning() -> Style {
    Style::default().fg(BRAND_YELLOW)
}

pub fn error() -> Style {
    Style::default().fg(BRAND_RED)
}

pub fn info() -> Style {
    Style::default().fg(BRAND_CYAN)
}

pub fn highlight() -> Style {
    Style::default()
        .fg(BRAND_BLUE)
        .add_modifier(Modifier::BOLD)
}

pub fn selected() -> Style {
    Style::default()
        .fg(Color::Black)
        .bg(BRAND_BLUE)
        .add_modifier(Modifier::BOLD)
}

pub fn key_hint() -> Style {
    Style::default()
        .fg(BRAND_YELLOW)
        .add_modifier(Modifier::BOLD)
}

pub const LOGO: &str = r#"
 ____            _ ____
| __ ) _   _ ___(_) __ )  _____  __
|  _ \| | | / __| |  _ \ / _ \ \/ /
| |_) | |_| \__ \ | |_) | (_) \  /
|____/ \__,_|___/_|____/ \___/\/\_\
"#;
