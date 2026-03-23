"""
이미지 속성값 검수 모듈 (간소화 버전)
- 손상된 파일, 너무 작은 파일, 형식 오류 등 명백한 이상만 검출
"""

import os
import glob
from PIL import Image


def validate_images(folder_path: str, pattern: str = "*.jpg", min_dpi: int = 300):
    """
    폴더 내 이미지 검수

    검출 항목:
    - 손상되어 열리지 않는 파일
    - 너무 작은 이미지 (500px 이하)
    - 너무 작은 파일 용량 (100KB 이하)
    - DPI 정보 없음 또는 낮음

    Returns:
        list[dict]: 각 파일의 검수 결과
    """
    image_files = sorted(glob.glob(os.path.join(folder_path, pattern)))

    if not image_files:
        print(f"'{folder_path}'에서 이미지를 찾을 수 없습니다.")
        return []

    results = []

    print(f"총 {len(image_files)}개 파일 검수 중...\n")

    for filepath in image_files:
        filename = os.path.basename(filepath)
        filesize_kb = os.path.getsize(filepath) / 1024
        file_errors = []

        result = {
            'filename': filename,
            'filepath': filepath,
            'filesize_kb': filesize_kb,
            'errors': []
        }

        # 1. 파일 용량 체크
        if filesize_kb < 100:
            file_errors.append(f"파일 용량 너무 작음: {filesize_kb:.1f}KB")

        # 2. 이미지 열기 시도
        try:
            with Image.open(filepath) as img:
                img.verify()

            with Image.open(filepath) as img:
                width, height = img.size
                dpi = img.info.get('dpi')

                result['width'] = width
                result['height'] = height
                result['dpi'] = dpi

                # 3. 크기 체크
                if width < 500 or height < 500:
                    file_errors.append(f"이미지 너무 작음: {width}x{height}px")

                # 4. DPI 체크
                if not dpi:
                    file_errors.append("DPI 정보 없음")
                elif dpi[0] < min_dpi:
                    file_errors.append(f"DPI 낮음: {dpi[0]} (최소 {min_dpi})")

        except Exception as e:
            file_errors.append(f"파일 손상/읽기 오류: {str(e)}")

        result['errors'] = file_errors
        results.append(result)

    # 결과 출력
    errors = [r for r in results if r['errors']]
    if errors:
        print(f"이상 파일 {len(errors)}건 발견:\n")
        for r in errors:
            print(f"  - {r['filename']}: {', '.join(r['errors'])}")
    else:
        print("모든 파일 정상")

    return results


if __name__ == "__main__":
    validate_images("03_이미지속성값 검수")
