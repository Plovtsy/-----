import {createRoot} from 'react-dom/client';
import App from './App';
import '../../static/style.css';
import styles from './tokens.module.css';
createRoot(document.getElementById('root')!).render(<div className={styles.app}><App/></div>);
