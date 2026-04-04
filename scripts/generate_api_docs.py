"""API 文档自动生成脚本——P1-005 实施"""

import ast
import sys
import json
from pathlib import Path
from typing import Any


def extract_docstring(node: ast.AST) -> str | None:
    """从 AST 节点提取 docstring."""
    return ast.get_docstring(node) or None


def extract_function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict:
    """提取函数签名信息."""
    args = node.args
    params = []
    
    # 基础参数
    for arg in args.args:
        annotation = None
        if arg.annotation:
            try:
                annotation = ast.unparse(arg.annotation)
            except (AttributeError, ValueError):
                annotation = None
        params.append({
            "name": arg.arg,
            "annotation": annotation,
            "kind": "positional"
        })
    
    # Keyword-only 参数
    for arg in args.kwonlyargs:
        annotation = None
        if arg.annotation:
            try:
                annotation = ast.unparse(arg.annotation)
            except (AttributeError, ValueError):
                annotation = None
        params.append({
            "name": arg.arg,
            "annotation": annotation,
            "kind": "keyword-only"
        })
    
    # 返回类型
    return_type = None
    if node.returns:
        try:
            return_type = ast.unparse(node.returns)
        except (AttributeError, ValueError):
            return_type = None
    
    return {
        "name": node.name,
        "is_async": isinstance(node, ast.AsyncFunctionDef),
        "params": params,
        "return_type": return_type,
        "docstring": extract_docstring(node),
    }


def parse_api_module(file_path: Path) -> dict:
    """解析 API 模块文件."""
    try:
        with open(file_path, encoding='utf-8') as f:
            content = f.read()
        tree = ast.parse(content)
    except Exception as e:
        print(f"⚠️  解析 {file_path} 失败: {e}", file=sys.stderr)
        return {"functions": [], "classes": []}
    
    functions = []
    classes = []
    
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                functions.append(extract_function_signature(node))
        elif isinstance(node, ast.ClassDef):
            if not node.name.startswith("_"):
                # 提取类的公开方法
                class_info = {
                    "name": node.name,
                    "docstring": extract_docstring(node),
                    "methods": [],
                }
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and not item.name.startswith("_"):
                        class_info["methods"].append(extract_function_signature(item))
                classes.append(class_info)
    
    return {
        "functions": functions,
        "classes": classes,
    }


def generate_markdown_doc(api_dir: Path) -> str:
    """生成 markdown 格式的 API 文档."""
    md = "# Sirius Chat API 文档\n\n"
    md += "自动生成的 Python API 参考文档。\n\n"
    
    api_files = sorted([f for f in api_dir.glob("*.py") if f.name != "__init__.py" and not f.name.startswith("__")])
    
    if not api_files:
        md += "（未找到 API 文件）\n"
        return md
    
    md += "## 模块索引\n\n"
    for api_file in api_files:
        module_name = api_file.stem
        md += f"- [{module_name}](#{module_name})\n"
    
    md += "\n---\n\n"
    
    # 生成详细文档
    for api_file in api_files:
        module_name = api_file.stem
        data = parse_api_module(api_file)
        
        if not data["functions"] and not data["classes"]:
            continue
        
        md += f"## {module_name}\n\n"
        
        # 类文档
        if data["classes"]:
            md += "### Classes\n\n"
            for cls in data["classes"]:
                md += f"#### `{cls['name']}`\n\n"
                if cls.get("docstring"):
                    md += f"{cls['docstring']}\n\n"
                
                # 方法
                if cls.get("methods"):
                    md += "**方法：**\n\n"
                    for method in cls["methods"]:
                        params = ", ".join(
                            f"{p['name']}: {p['annotation']}" if p['annotation'] else p['name']
                            for p in method['params']
                        )
                        sig = f"{'async ' if method['is_async'] else ''}{method['name']}({params})"
                        if method['return_type']:
                            sig += f" -> {method['return_type']}"
                        
                        md += f"- `{sig}`"
                        if method['docstring']:
                            first_line = method['docstring'].split('\\n')[0]
                            md += f" - {first_line}"
                        md += "\n"
                    md += "\n"
        
        # 函数文档
        if data["functions"]:
            md += "### Functions\n\n"
            for func in data["functions"]:
                params = ", ".join(
                    f"{p['name']}: {p['annotation']}" if p['annotation'] else p['name']
                    for p in func['params']
                )
                sig = f"{'async ' if func['is_async'] else ''}{func['name']}({params})"
                if func['return_type']:
                    sig += f" -> {func['return_type']}"
                
                md += f"#### `{sig}`\n\n"
                if func['docstring']:
                    md += f"{func['docstring']}\n\n"
        
        md += "\n---\n\n"
    
    return md


def generate_json_doc(api_dir: Path) -> dict:
    """生成 JSON 格式的 API 文档."""
    api_files = sorted([f for f in api_dir.glob("*.py") if f.name != "__init__.py" and not f.name.startswith("__")])
    
    result = {
        "title": "Sirius Chat API Reference",
        "version": "1.0.0",
        "modules": {}
    }
    
    for api_file in api_files:
        module_name = api_file.stem
        result["modules"][module_name] = parse_api_module(api_file)
    
    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python generate_api_docs.py <format> [<output_path>]")
        print("Formats: markdown, json")
        sys.exit(1)
    
    output_format = sys.argv[1]
    api_dir = Path(__file__).parent.parent / "sirius_chat" / "api"
    
    if not api_dir.exists():
        print(f"❌ API 目录不存在: {api_dir}")
        sys.exit(1)
    
    if output_format == "markdown":
        output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("docs/api.md")
        doc = generate_markdown_doc(api_dir)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(doc, encoding='utf-8')
        print(f"✅ Markdown API 文档已生成: {output_path}")
    
    elif output_format == "json":
        output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("docs/api.json")
        doc = generate_json_doc(api_dir)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(doc, f, indent=2, ensure_ascii=False)
        print(f"✅ JSON API 文档已生成: {output_path}")
    
    else:
        print(f"❌ 不支持的格式: {output_format}")
        sys.exit(1)

