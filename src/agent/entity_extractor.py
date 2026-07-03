from typing import List, Dict, Tuple, Optional, Any
import re

# todo 实体提取器
class EntityExtractor:
    def __init__(self):
        self.time_patterns = [
            (r'(\d{4})年(\d{1,2})月(\d{1,2})日', 'date'),
            (r'(\d{4})-(\d{1,2})-(\d{1,2})', 'date'),
            (r'(\d{4})/(\d{1,2})/(\d{1,2})', 'date'),
            (r'(\d{1,2})月(\d{1,2})日', 'date'),
            (r'(\d{1,2})日', 'date'),
            (r'(\d{1,2}):(\d{2})', 'time'),
            (r'(\d{1,2})点(\d{2})分', 'time'),
            (r'(\d{1,2})点', 'time'),
            (r'今天', 'relative_date'),
            (r'明天', 'relative_date'),
            (r'昨天', 'relative_date'),
            (r'本周', 'relative_date'),
            (r'下周', 'relative_date'),
            (r'上周', 'relative_date'),
            (r'本月', 'relative_date'),
            (r'下月', 'relative_date'),
            (r'上月', 'relative_date'),
            (r'今年', 'relative_date'),
            (r'明年', 'relative_date'),
            (r'去年', 'relative_date'),
            (r'周末', 'relative_date'),
            (r'周一|周二|周三|周四|周五|周六|周日', 'weekday'),
        ]
        
        self.location_patterns = [
            (r'在(\S+)', 'location'),
            (r'到(\S+)', 'location'),
            (r'去(\S+)', 'location'),
            (r'从(\S+)到(\S+)', 'route'),
            (r'(\S+)旅游', 'destination'),
            (r'(\S+)游玩', 'destination'),
            (r'(\S+)出差', 'destination'),
            (r'(\S+)开会', 'destination'),
        ]
        
        self.quantity_patterns = [
            (r'(\d+)\s*篇', 'count'),
            (r'(\d+)\s*个', 'count'),
            (r'(\d+)\s*份', 'count'),
            (r'(\d+)\s*本', 'count'),
            (r'(\d+)\s*页', 'count'),
            (r'(\d+)\s*章', 'count'),
            (r'(\d+)\s*节', 'count'),
            (r'(\d+)\s*天', 'duration'),
            (r'(\d+)\s*小时', 'duration'),
            (r'(\d+)\s*分钟', 'duration'),
            (r'(\d+)\s*秒', 'duration'),
            (r'(\d+)\s*周', 'duration'),
            (r'(\d+)\s*月', 'duration'),
            (r'(\d+)\s*年', 'duration'),
            (r'(\d+\.?\d*)\s*%', 'percentage'),
            (r'(\d+\.?\d*)\s*元', 'currency'),
            (r'(\d+\.?\d*)\s*美元', 'currency'),
            (r'(\d+\.?\d*)\s*人民币', 'currency'),
            (r'(\d+\.?\d*)\s*万', 'currency'),
            (r'(\d+\.?\d*)\s*亿', 'currency'),
            (r'(\d+\.?\d*)\s*千', 'currency'),
            (r'(\d+\.?\d*)\s*百', 'currency'),
        ]
        
        self.file_patterns = [
            (r'(\S+\.txt)', 'txt_file'),
            (r'(\S+\.doc)', 'doc_file'),
            (r'(\S+\.docx)', 'docx_file'),
            (r'(\S+\.pdf)', 'pdf_file'),
            (r'(\S+\.xlsx)', 'excel_file'),
            (r'(\S+\.xls)', 'excel_file'),
            (r'(\S+\.csv)', 'csv_file'),
            (r'(\S+\.json)', 'json_file'),
            (r'(\S+\.xml)', 'xml_file'),
            (r'(\S+\.html)', 'html_file'),
            (r'(\S+\.md)', 'markdown_file'),
            (r'(\S+\.py)', 'python_file'),
            (r'(\S+\.java)', 'java_file'),
            (r'(\S+\.cpp)', 'cpp_file'),
            (r'(\S+\.js)', 'js_file'),
            (r'(\S+\.ts)', 'ts_file'),
            (r'(\S+\.go)', 'go_file'),
            (r'(\S+\.sql)', 'sql_file'),
        ]
        
        self.number_patterns = [
            (r'(\d+\.?\d*)', 'number'),
            (r'(\d+)\s*×\s*(\d+)', 'multiplication'),
            (r'(\d+)\s*÷\s*(\d+)', 'division'),
            (r'(\d+)\s*\+\s*(\d+)', 'addition'),
            (r'(\d+)\s*-\s*(\d+)', 'subtraction'),
        ]
        
        self.person_patterns = [
            (r'(\S+)说', 'speaker'),
            (r'(\S+)认为', 'opinion_holder'),
            (r'(\S+)觉得', 'opinion_holder'),
            (r'(\S+)建议', 'advisor'),
            (r'(\S+)推荐', 'recommender'),
        ]
    
    def extract_time(self, text: str) -> List[Dict[str, Any]]:
        results = []
        for pattern, entity_type in self.time_patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                if isinstance(match, tuple):
                    results.append({
                        'type': entity_type,
                        'value': ''.join(match),
                        'raw': match
                    })
                else:
                    results.append({
                        'type': entity_type,
                        'value': match,
                        'raw': match
                    })
        return results
    
    def extract_location(self, text: str) -> List[Dict[str, Any]]:
        results = []
        for pattern, entity_type in self.location_patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                if isinstance(match, tuple):
                    results.append({
                        'type': entity_type,
                        'value': '到'.join(match),
                        'raw': match
                    })
                else:
                    results.append({
                        'type': entity_type,
                        'value': match,
                        'raw': match
                    })
        return results
    
    def extract_quantity(self, text: str) -> List[Dict[str, Any]]:
        results = []
        for pattern, entity_type in self.quantity_patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                if isinstance(match, tuple):
                    value = match[0]
                    try:
                        value = float(value)
                    except:
                        pass
                    results.append({
                        'type': entity_type,
                        'value': value,
                        'raw': match
                    })
                else:
                    try:
                        value = float(match)
                    except:
                        value = match
                    results.append({
                        'type': entity_type,
                        'value': value,
                        'raw': match
                    })
        return results
    
    def extract_files(self, text: str) -> List[Dict[str, Any]]:
        results = []
        for pattern, entity_type in self.file_patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                results.append({
                    'type': entity_type,
                    'value': match,
                    'raw': match
                })
        return results
    
    def extract_numbers(self, text: str) -> List[Dict[str, Any]]:
        results = []
        for pattern, entity_type in self.number_patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                if isinstance(match, tuple):
                    values = []
                    for m in match:
                        try:
                            values.append(float(m))
                        except:
                            values.append(m)
                    results.append({
                        'type': entity_type,
                        'value': values,
                        'raw': match
                    })
                else:
                    try:
                        value = float(match)
                    except:
                        value = match
                    results.append({
                        'type': entity_type,
                        'value': value,
                        'raw': match
                    })
        return results
    
    def extract_person(self, text: str) -> List[Dict[str, Any]]:
        results = []
        for pattern, entity_type in self.person_patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                results.append({
                    'type': entity_type,
                    'value': match,
                    'raw': match
                })
        return results
    
    def extract_all(self, text: str) -> Dict[str, Any]:
        """
        提取所有实体和参数
        返回：包含各类实体的字典
        """
        entities = []
        parameters = {}
        
        time_entities = self.extract_time(text)
        location_entities = self.extract_location(text)
        quantity_entities = self.extract_quantity(text)
        file_entities = self.extract_files(text)
        number_entities = self.extract_numbers(text)
        person_entities = self.extract_person(text)
        
        entities.extend(time_entities)
        entities.extend(location_entities)
        entities.extend(file_entities)
        entities.extend(person_entities)
        
        for entity in quantity_entities:
            if entity['type'] == 'count':
                parameters['count'] = entity['value']
            elif entity['type'] == 'duration':
                parameters['duration'] = entity['value']
            elif entity['type'] == 'percentage':
                parameters['percentage'] = entity['value']
            elif entity['type'] == 'currency':
                parameters['amount'] = entity['value']
        
        for entity in time_entities:
            if entity['type'] == 'date':
                parameters['date'] = entity['value']
            elif entity['type'] == 'time':
                parameters['time'] = entity['value']
            elif entity['type'] == 'relative_date':
                parameters['relative_date'] = entity['value']
        
        for entity in location_entities:
            if entity['type'] == 'location':
                parameters['location'] = entity['value']
            elif entity['type'] == 'destination':
                parameters['destination'] = entity['value']
        
        for entity in file_entities:
            parameters['file'] = entity['value']
        
        for entity in number_entities:
            if entity['type'] == 'number':
                parameters['number'] = entity['value']
        
        return {
            'entities': entities,
            'parameters': parameters
        }