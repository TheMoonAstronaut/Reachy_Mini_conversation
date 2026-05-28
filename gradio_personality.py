"""Gradio personality UI components."""

from __future__ import annotations
from typing import Any
from pathlib import Path

import gradio as gr

from config import DEFAULT_PROFILES_DIRECTORY


class PersonalityUI:
    def __init__(self) -> None:
        self.DEFAULT_OPTION = "(built-in default)"
        self._profiles_root = DEFAULT_PROFILES_DIRECTORY

        self.personalities_dropdown: gr.Dropdown
        self.apply_btn: gr.Button
        self.status_md: gr.Markdown
        self.preview_md: gr.Markdown
        self.person_name_tb: gr.Textbox
        self.person_instr_ta: gr.TextArea
        self.tools_txt_ta: gr.TextArea
        self.voice_dropdown: gr.Dropdown
        self.new_personality_btn: gr.Button
        self.available_tools_cg: gr.CheckboxGroup
        self.save_btn: gr.Button

    def _list_personalities(self) -> list[str]:
        names: list[str] = []
        try:
            if self._profiles_root.exists():
                for p in sorted(self._profiles_root.iterdir()):
                    if p.is_dir() and (p / "instructions.txt").exists():
                        names.append(p.name)
        except Exception:
            pass
        return names

    def _resolve_profile_dir(self, selection: str) -> Path:
        return self._profiles_root / selection

    def _read_instructions_for(self, name: str) -> str:
        try:
            if name == self.DEFAULT_OPTION:
                default_file = self._profiles_root / "default" / "instructions.txt"
                if default_file.exists():
                    return default_file.read_text(encoding="utf-8").strip()
                return ""
            target = self._resolve_profile_dir(name) / "instructions.txt"
            if target.exists():
                return target.read_text(encoding="utf-8").strip()
            return ""
        except Exception as e:
            return f"Could not load instructions: {e}"

    def _read_tools_for(self, name: str) -> str:
        try:
            profile_name = "default" if name == self.DEFAULT_OPTION else name
            target = self._resolve_profile_dir(profile_name) / "tools.txt"
            if target.exists():
                return target.read_text(encoding="utf-8")
        except Exception:
            pass
        return ""

    def create_components(self) -> None:
        current_value = self.DEFAULT_OPTION
        dropdown_choices = [self.DEFAULT_OPTION, *(self._list_personalities())]

        self.personalities_dropdown = gr.Dropdown(
            label="Select personality",
            choices=dropdown_choices,
            value=current_value,
        )
        self.apply_btn = gr.Button("Apply personality")
        self.status_md = gr.Markdown(visible=True)
        self.preview_md = gr.Markdown(value=self._read_instructions_for(current_value))
        self.person_name_tb = gr.Textbox(label="Personality name")
        self.person_instr_ta = gr.TextArea(label="Personality instructions", lines=10)
        self.tools_txt_ta = gr.TextArea(
            label="tools.txt",
            value=self._read_tools_for(current_value),
            lines=10,
        )
        self.voice_dropdown = gr.Dropdown(
            label="Voice",
            choices=["zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural", "zh-CN-YunyangNeural"],
            value="zh-CN-XiaoxiaoNeural",
        )
        self.new_personality_btn = gr.Button("New personality")
        self.available_tools_cg = gr.CheckboxGroup(
            label="Available tools",
            choices=["dance", "stop_dance", "move_head", "idle_do_nothing"],
            value=["dance", "stop_dance", "move_head", "idle_do_nothing"],
        )
        self.save_btn = gr.Button("Save personality")

    def additional_inputs_ordered(self) -> list[Any]:
        return [
            self.personalities_dropdown,
            self.apply_btn,
            self.new_personality_btn,
            self.status_md,
            self.preview_md,
            self.person_name_tb,
            self.person_instr_ta,
            self.tools_txt_ta,
            self.voice_dropdown,
            self.available_tools_cg,
            self.save_btn,
        ]

    def wire_events(self, handler: Any, blocks: gr.Blocks) -> None:
        async def _apply_personality(selected: str) -> tuple[str, str]:
            status = f"Profile applied: {selected}"
            preview = self._read_instructions_for(selected)
            return status, preview

        def _load_profile_for_edit(selected: str) -> tuple[Any, Any, str]:
            instr = self._read_instructions_for(selected)
            tools_txt = self._read_tools_for(selected)
            status_text = f"Loaded profile '{selected}'."
            return (
                gr.update(value=instr),
                gr.update(value=tools_txt),
                status_text,
            )

        def _new_personality() -> tuple[Any, Any, Any, Any, str, Any]:
            return (
                gr.update(value=""),
                gr.update(value="# Write your instructions here\n"),
                gr.update(value="dance\nstop_dance\nmove_head\nidle_do_nothing"),
                gr.update(),
                "Fill in a name, instructions and tools, then Save.",
                gr.update(value="zh-CN-XiaoxiaoNeural"),
            )

        def _save_personality(
            name: str, instructions: str, tools_text: str, voice: str
        ) -> tuple[Any, Any, str]:
            if not name.strip():
                return gr.update(), gr.update(), "Please enter a valid name."
            try:
                target_dir = self._profiles_root / name.strip()
                target_dir.mkdir(parents=True, exist_ok=True)
                (target_dir / "instructions.txt").write_text(instructions.strip() + "\n", encoding="utf-8")
                (target_dir / "tools.txt").write_text(tools_text.strip() + "\n", encoding="utf-8")
                (target_dir / "voice.txt").write_text(voice.strip() + "\n", encoding="utf-8")

                choices = self._list_personalities()
                return (
                    gr.update(choices=[self.DEFAULT_OPTION, *sorted(choices)], value=name.strip()),
                    gr.update(value=instructions),
                    f"Saved personality '{name.strip()}'.",
                )
            except Exception as e:
                return gr.update(), gr.update(), f"Failed to save personality: {e}"

        with blocks:
            self.apply_btn.click(
                fn=_apply_personality,
                inputs=[self.personalities_dropdown],
                outputs=[self.status_md, self.preview_md],
            )

            self.personalities_dropdown.change(
                fn=_load_profile_for_edit,
                inputs=[self.personalities_dropdown],
                outputs=[self.person_instr_ta, self.tools_txt_ta, self.status_md],
            )

            self.new_personality_btn.click(
                fn=_new_personality,
                inputs=[],
                outputs=[
                    self.person_name_tb,
                    self.person_instr_ta,
                    self.tools_txt_ta,
                    self.available_tools_cg,
                    self.status_md,
                    self.voice_dropdown,
                ],
            )

            self.save_btn.click(
                fn=_save_personality,
                inputs=[self.person_name_tb, self.person_instr_ta, self.tools_txt_ta, self.voice_dropdown],
                outputs=[self.personalities_dropdown, self.person_instr_ta, self.status_md],
            ).then(
                fn=_apply_personality,
                inputs=[self.personalities_dropdown],
                outputs=[self.status_md, self.preview_md],
            )