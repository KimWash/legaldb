#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
HWP/HWPX 파일 텍스트 추출기
HWP(한글 워드프로세서) 및 HWPX(OWPML) 파일에서 텍스트를 추출하는 스크립트

사용법:
    python hwp_reader.py <file_path>
    python hwp_reader.py <file_path> -o <output_file>
    python hwp_reader.py <file_path> --preview

필요 라이브러리:
    pip install olefile
"""

import argparse
import os
import struct
import sys
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path

try:
    import olefile
except ImportError:
    print("olefile 라이브러리가 필요합니다. 설치해주세요:")
    print("  pip install olefile")
    sys.exit(1)


class HWPReader:
    """HWP 파일에서 텍스트를 추출하는 클래스"""

    # HWP 제어 문자 정의
    CHAR_EXTENDED = 1      # 확장 문자
    CHAR_SECTION = 2       # 섹션/단 정의
    CHAR_FIELD_START = 3   # 필드 시작
    CHAR_FIELD_END = 4     # 필드 끝
    CHAR_FOOTNOTE = 5      # 각주/미주
    CHAR_TABLE = 6         # 테이블/그림
    CHAR_EMPTY = 7         # 빈줄
    CHAR_HEADER = 8        # 머리말/꼬리말
    CHAR_ENDNOTE = 9       # 미주
    CHAR_NEWLINE = 10      # 줄바꿈
    CHAR_DRAWING = 11      # 그리기 개체
    CHAR_DRAWING_END = 12  # 그리기 개체 끝
    CHAR_PARA_END = 13     # 문단 끝
    CHAR_SPACE = 14        # 빈칸
    CHAR_HIDDEN = 15       # 숨은 설명
    CHAR_TAB = 24          # 탭

    # 16바이트 점프가 필요한 제어 문자들
    SKIP_16_BYTES = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 30}

    def __init__(self, file_path: str):
        """
        Args:
            file_path: HWP 파일 경로
        """
        self.file_path = Path(file_path)
        if not self.file_path.exists():
            raise FileNotFoundError(f"파일을 찾을 수 없습니다: {file_path}")

        if not self.file_path.suffix.lower() == '.hwp':
            raise ValueError(f"HWP 파일이 아닙니다: {file_path}")

        self.ole = None
        self.is_compressed = False

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def open(self):
        """HWP 파일 열기"""
        self.ole = olefile.OleFileIO(str(self.file_path))

        # FileHeader에서 압축 여부 확인
        header = self.ole.openstream('FileHeader').read()
        self.is_compressed = bool(header[36] & 1)

    def close(self):
        """HWP 파일 닫기"""
        if self.ole:
            self.ole.close()
            self.ole = None

    def get_preview_text(self) -> str:
        """
        PrvText 스트림에서 미리보기 텍스트 추출
        (빠르지만 전체 내용이 아닐 수 있음)

        Returns:
            추출된 미리보기 텍스트
        """
        if not self.ole:
            self.open()

        if not self.ole.exists('PrvText'):
            return ""

        prvtext_data = self.ole.openstream('PrvText').read()
        text = prvtext_data.decode('utf-16-le', errors='ignore')
        text = text.replace('\x00', '')
        return text.strip()

    def get_full_text(self) -> str:
        """
        BodyText 스트림에서 전체 텍스트 추출

        Returns:
            추출된 전체 텍스트
        """
        if not self.ole:
            self.open()

        text_parts = []

        # BodyText 섹션들 순회
        for entry in self.ole.listdir():
            path = "/".join(entry)
            if not path.startswith("BodyText/Section"):
                continue

            section_text = self._extract_section_text(entry)
            if section_text:
                text_parts.append(section_text)

        result = '\n'.join(text_parts)

        # 연속 줄바꿈 정리 (3개 이상 -> 2개)
        import re
        result = re.sub(r'\n{3,}', '\n\n', result)

        return result.strip()

    def _extract_section_text(self, entry) -> str:
        """
        개별 섹션에서 텍스트 추출

        Args:
            entry: OLE 스트림 엔트리

        Returns:
            추출된 섹션 텍스트
        """
        data = self.ole.openstream(entry).read()

        # 압축 해제
        if self.is_compressed:
            try:
                data = zlib.decompress(data, -15)
            except zlib.error:
                return ""

        text_chars = []
        pos = 0

        while pos < len(data):
            if pos + 4 > len(data):
                break

            # 레코드 헤더 읽기
            header = struct.unpack('<I', data[pos:pos+4])[0]
            tag_id = header & 0x3FF
            size = (header >> 20) & 0xFFF

            # 확장 크기 처리
            if size == 0xFFF:
                if pos + 8 > len(data):
                    break
                size = struct.unpack('<I', data[pos+4:pos+8])[0]
                pos += 8
            else:
                pos += 4

            # HWPTAG_PARA_TEXT (67) 처리
            if tag_id == 67:
                record_data = data[pos:pos+size]
                text_chars.extend(self._parse_para_text(record_data))

            pos += size

        return ''.join(text_chars)

    def _parse_para_text(self, record_data: bytes) -> list:
        """
        PARA_TEXT 레코드에서 텍스트 파싱

        Args:
            record_data: 레코드 데이터

        Returns:
            문자 리스트
        """
        chars = []
        i = 0

        while i < len(record_data) - 1:
            char_code = struct.unpack('<H', record_data[i:i+2])[0]

            if char_code == 0:
                i += 2
            elif char_code in self.SKIP_16_BYTES:
                # 특수 제어 문자 - 16바이트 점프
                if char_code == self.CHAR_TAB:
                    chars.append('\t')
                i += 16
            elif char_code == self.CHAR_NEWLINE:
                chars.append('\n')
                i += 2
            elif char_code == self.CHAR_DRAWING_END:
                i += 2
            elif char_code == self.CHAR_PARA_END:
                chars.append('\n')
                i += 2
            elif char_code == 31:
                i += 2
            elif char_code < 32:
                i += 2
            else:
                # 일반 문자
                try:
                    chars.append(chr(char_code))
                except ValueError:
                    pass
                i += 2

        return chars

    def get_file_info(self) -> dict:
        """
        HWP 파일 정보 반환

        Returns:
            파일 정보 딕셔너리
        """
        if not self.ole:
            self.open()

        info = {
            'file_name': self.file_path.name,
            'file_size': self.file_path.stat().st_size,
            'is_compressed': self.is_compressed,
            'streams': ["/".join(entry) for entry in self.ole.listdir()]
        }

        return info


class HWPXReader:
    """HWPX(OWPML) 파일에서 텍스트를 추출하는 클래스"""

    # OWPML 네임스페이스
    NAMESPACES = {
        'hp': 'http://www.hancom.co.kr/hwpml/2011/paragraph',
        'hs': 'http://www.hancom.co.kr/hwpml/2011/section',
        'hc': 'http://www.hancom.co.kr/hwpml/2011/core',
    }

    def __init__(self, file_path: str):
        """
        Args:
            file_path: HWPX 파일 경로
        """
        self.file_path = Path(file_path)
        if not self.file_path.exists():
            raise FileNotFoundError(f"파일을 찾을 수 없습니다: {file_path}")

        if not self.file_path.suffix.lower() == '.hwpx':
            raise ValueError(f"HWPX 파일이 아닙니다: {file_path}")

        self.zf = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def open(self):
        """HWPX 파일 열기 (ZIP 형식)"""
        try:
            self.zf = zipfile.ZipFile(str(self.file_path), 'r')
        except zipfile.BadZipFile:
            raise ValueError(f"유효한 HWPX(ZIP) 파일이 아닙니다: {self.file_path}")

    def close(self):
        """HWPX 파일 닫기"""
        if self.zf:
            self.zf.close()
            self.zf = None

    def _get_section_files(self) -> list:
        """Contents/section*.xml 파일 목록을 정렬된 순서로 반환"""
        section_files = []
        for name in self.zf.namelist():
            lower = name.lower()
            if lower.startswith('contents/section') and lower.endswith('.xml'):
                section_files.append(name)
        section_files.sort()
        return section_files

    def _extract_text_from_xml(self, xml_data: bytes) -> str:
        """
        XML 데이터에서 텍스트 추출

        Args:
            xml_data: section XML 바이트 데이터

        Returns:
            추출된 텍스트
        """
        try:
            root = ET.fromstring(xml_data)
        except ET.ParseError:
            return ""

        text_parts = []

        # hp:t 태그에서 텍스트 추출 (OWPML 본문 텍스트 태그)
        for t_elem in root.iter():
            tag = t_elem.tag
            # 네임스페이스 포함/미포함 모두 처리
            local_name = tag.split('}')[-1] if '}' in tag else tag

            if local_name == 't':
                if t_elem.text:
                    text_parts.append(t_elem.text)
            elif local_name == 'p':
                # 문단 구분을 위해 줄바꿈 추가
                if text_parts and not text_parts[-1].endswith('\n'):
                    text_parts.append('\n')

        return ''.join(text_parts)

    def get_full_text(self) -> str:
        """
        모든 section XML에서 전체 텍스트 추출

        Returns:
            추출된 전체 텍스트
        """
        if not self.zf:
            self.open()

        text_parts = []
        for section_file in self._get_section_files():
            xml_data = self.zf.read(section_file)
            section_text = self._extract_text_from_xml(xml_data)
            if section_text:
                text_parts.append(section_text)

        result = '\n'.join(text_parts)

        # 연속 줄바꿈 정리 (3개 이상 -> 2개)
        import re
        result = re.sub(r'\n{3,}', '\n\n', result)

        return result.strip()

    def get_preview_text(self) -> str:
        """
        미리보기 텍스트 추출
        HWPX에는 PrvText가 없으므로 첫 번째 섹션의 앞부분을 반환

        Returns:
            추출된 미리보기 텍스트
        """
        if not self.zf:
            self.open()

        # Preview/PrvText.txt가 있으면 사용
        for name in self.zf.namelist():
            lower = name.lower()
            if 'prvtext' in lower or (lower.startswith('preview/') and lower.endswith('.txt')):
                try:
                    data = self.zf.read(name)
                    text = data.decode('utf-8', errors='ignore')
                    if not text.strip():
                        text = data.decode('utf-16-le', errors='ignore')
                    return text.strip()
                except Exception:
                    pass

        # 없으면 전체 텍스트의 앞부분 반환
        full_text = self.get_full_text()
        if len(full_text) > 1000:
            return full_text[:1000] + "..."
        return full_text

    def get_file_info(self) -> dict:
        """
        HWPX 파일 정보 반환

        Returns:
            파일 정보 딕셔너리
        """
        if not self.zf:
            self.open()

        info = {
            'file_name': self.file_path.name,
            'file_size': self.file_path.stat().st_size,
            'format': 'HWPX (OWPML)',
            'entries': self.zf.namelist(),
            'section_count': len(self._get_section_files()),
        }

        return info


def main():
    parser = argparse.ArgumentParser(
        description='HWP/HWPX 파일에서 텍스트를 추출합니다.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예제:
  python hwp_reader.py document.hwp
  python hwp_reader.py document.hwpx
  python hwp_reader.py document.hwp -o output.txt
  python hwp_reader.py document.hwpx --preview
  python hwp_reader.py document.hwp --info
        """
    )

    parser.add_argument('file', help='HWP 또는 HWPX 파일 경로')
    parser.add_argument('-o', '--output', help='출력 파일 경로 (지정하지 않으면 콘솔에 출력)')
    parser.add_argument('--preview', action='store_true', help='미리보기 텍스트만 추출 (빠름)')
    parser.add_argument('--info', action='store_true', help='파일 정보 출력')
    parser.add_argument('--encoding', default='utf-8', help='출력 파일 인코딩 (기본값: utf-8)')

    args = parser.parse_args()

    try:
        # 확장자에 따라 적절한 Reader 선택
        ext = Path(args.file).suffix.lower()
        if ext == '.hwpx':
            reader_class = HWPXReader
        elif ext == '.hwp':
            reader_class = HWPReader
        else:
            print(f"오류: 지원하지 않는 파일 형식입니다: {ext}", file=sys.stderr)
            print("지원 형식: .hwp, .hwpx", file=sys.stderr)
            sys.exit(1)

        with reader_class(args.file) as reader:
            if args.info:
                info = reader.get_file_info()
                print(f"파일명: {info['file_name']}")
                print(f"파일 크기: {info['file_size']:,} bytes")
                if ext == '.hwp':
                    print(f"압축 여부: {'예' if info['is_compressed'] else '아니오'}")
                    print(f"스트림 목록:")
                    for stream in info['streams']:
                        print(f"  - {stream}")
                else:
                    print(f"형식: {info['format']}")
                    print(f"섹션 수: {info['section_count']}")
                    print(f"내부 파일 목록:")
                    for entry in info['entries']:
                        print(f"  - {entry}")
                return

            if args.preview:
                text = reader.get_preview_text()
            else:
                text = reader.get_full_text()

            if args.output:
                output_path = Path(args.output)
                output_path.write_text(text, encoding=args.encoding)
                print(f"텍스트를 저장했습니다: {output_path}")
            else:
                print(text)

    except FileNotFoundError as e:
        print(f"오류: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"오류: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"오류 발생: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
